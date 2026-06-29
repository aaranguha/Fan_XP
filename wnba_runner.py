"""
wnba_runner.py

Checks today's WNBA schedule via ESPN and launches wnba_run_game.py
for each team with a home game today. All teams run in parallel.

Usage:
    python wnba_runner.py [YYYY-MM-DD]
"""

import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta

import requests

from wnba_teams import WNBA_TEAMS, ESPN_ABBR_TO_SLUG

PYTHON = sys.executable
PRE_GAME_OFFSET_MIN = 60
MAX_WAIT_MIN        = 120  # only launch games whose pre-game is within 2 hours


def get_home_teams_espn(today: str) -> list[tuple[str, str]]:
    """Returns list of (slug, game_time_utc) tuples."""
    date_str = today.replace("-", "")
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={date_str}"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()

    results = []
    for event in resp.json().get("events", []):
        comp = event["competitions"][0]
        home = next((t for t in comp["competitors"] if t["homeAway"] == "home"), None)
        away = next((t for t in comp["competitors"] if t["homeAway"] == "away"), None)
        if not home:
            continue
        abbr = home["team"]["abbreviation"]
        slug = ESPN_ABBR_TO_SLUG.get(abbr)
        if slug:
            game_time = event.get("date", "")
            home_name = home["team"]["displayName"]
            away_name = away["team"]["displayName"] if away else "?"
            print(f"  Home game: {away_name} at {home_name}  →  {slug}  time: {game_time}")
            results.append((slug, game_time))
        else:
            print(f"  Unknown ESPN abbreviation: {abbr} — skipping")
    return results


def main():
    if len(sys.argv) > 1:
        today = sys.argv[1]
    else:
        today = os.getenv("WNBA_DATE") or datetime.now().strftime("%Y-%m-%d")
    print(f"[{today}] Checking today's WNBA schedule...")

    try:
        games = get_home_teams_espn(today)
    except Exception as e:
        print(f"Error fetching WNBA schedule: {e}")
        sys.exit(1)

    if not games:
        print("  No WNBA home games today.")
        sys.exit(0)

    # Only launch games whose pre-game window is within the next 6 hours
    now_utc  = datetime.now(timezone.utc)
    eligible = []
    for slug, game_time_str in games:
        if not game_time_str:
            eligible.append(slug)
            continue
        try:
            game_utc = datetime.fromisoformat(game_time_str.replace("Z", "+00:00"))
            pregame  = game_utc - timedelta(minutes=PRE_GAME_OFFSET_MIN)
            wait_min = (pregame - now_utc).total_seconds() / 60
            if wait_min <= MAX_WAIT_MIN:
                eligible.append(slug)
            else:
                print(f"  Skipping {slug} — pre-game in {wait_min:.0f} min (next cron will catch it)")
        except Exception:
            eligible.append(slug)

    if not eligible:
        print("  No games within the next 6 hours — next cron will handle them.")
        sys.exit(0)

    print(f"\n  Launching {len(eligible)} game runner(s)...\n")
    procs = []
    for slug in eligible:
        log_dir  = f"data/wnba/{slug}"
        os.makedirs(log_dir, exist_ok=True)
        log_path = f"{log_dir}/game.log"
        log_file = open(log_path, "a")
        proc = subprocess.Popen(
            [PYTHON, "wnba_run_game.py", slug, today],
            stdout=log_file,
            stderr=log_file,
        )
        procs.append((slug, proc, log_file))
        print(f"  Launched {slug} (PID {proc.pid}) → {log_path}")

    print("\n  All runners launched. Waiting for completion...\n")
    last_lines = {}
    for slug, proc, log_file in procs:
        while proc.poll() is None:
            import time; time.sleep(60)
            log_path = f"data/wnba/{slug}/game.log"
            if os.path.isfile(log_path):
                try:
                    with open(log_path) as lf:
                        lines = lf.readlines()
                    last = next((l.rstrip() for l in reversed(lines) if l.strip()), "")
                    if last and last != last_lines.get(slug):
                        last_lines[slug] = last
                        print(f"  [{slug}] {last}", flush=True)
                except Exception:
                    pass
        log_file.close()

    succeeded = [s for s, proc, _ in procs if proc.returncode == 0]
    failed    = [s for s, proc, _ in procs if proc.returncode != 0]

    if succeeded:
        teams_str = "-".join(sorted(succeeded))
        subprocess.run(["git", "add", "data/wnba/", "docs/"], check=True)
        commit = subprocess.run([
            "git", "commit", "-m", f"WNBA auto-update {today}: {teams_str}"
        ])
        if commit.returncode == 0:
            result = subprocess.run(["git", "push"])
            print("  Pushed to GitHub." if result.returncode == 0 else "  git push failed.")
        else:
            print("  Nothing new to commit.")

    if failed:
        print(f"\n  Failed: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
