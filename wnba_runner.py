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
from dotenv import load_dotenv

from wnba_teams import WNBA_TEAMS

load_dotenv()

PYTHON = sys.executable
PRE_GAME_OFFSET_MIN = 60
MAX_WAIT_MIN        = 120  # only launch games whose pre-game is within 2 hours


def get_home_teams_tm(today: str) -> list[tuple[str, str]]:
    """
    Fetch today's WNBA home teams via the Ticketmaster Discovery API.

    Replaces the old ESPN scoreboard lookup, which started 403ing (confirmed
    live, even from this self-hosted residential IP). Requiring BOTH team
    names in an event to match one of our 12 real WNBA_TEAMS filters out
    non-game noise (open practices, hospitality add-ons) for free. A team's
    own keyword can also turn up in their AWAY games, so we take the
    leftmost-mentioned team as home rather than trusting the search keyword.

    Returns list of (slug, game_time_utc_iso) tuples, same shape the old
    ESPN-based function returned, so the rest of main() is unchanged.
    """
    tm_api_key = os.getenv("TICKETMASTER_API_KEY", "").strip()
    if not tm_api_key:
        raise RuntimeError("TICKETMASTER_API_KEY not set in .env")

    resp = requests.get(
        "https://app.ticketmaster.com/discovery/v2/events.json",
        params={
            "apikey":             tm_api_key,
            "classificationName": "Basketball",
            "countryCode":        "US",
            "localStartDateTime": f"{today}T00:00:00,{today}T23:59:59",
            "sort":               "date,asc",
            "size":               200,
        },
        timeout=15,
    )
    resp.raise_for_status()
    events = resp.json().get("_embedded", {}).get("events", [])

    results = []
    for event in events:
        name = event.get("name", "")
        name_lower = name.lower()

        matches = []  # (index, slug)
        for slug, team in WNBA_TEAMS.items():
            idx = name_lower.find(team["tm_keyword"].lower())
            if idx >= 0:
                matches.append((idx, slug))
        if len(matches) != 2:
            continue  # not a real WNBA-vs-WNBA matchup

        matches.sort()
        home_slug = matches[0][1]
        game_time = event.get("dates", {}).get("start", {}).get("dateTime", "")
        print(f"  Home game: {name}  →  {home_slug}  time: {game_time}")
        results.append((home_slug, game_time))
    return results


def main():
    if len(sys.argv) > 1:
        today = sys.argv[1]
    else:
        today = os.getenv("WNBA_DATE") or datetime.now().strftime("%Y-%m-%d")
    print(f"[{today}] Checking today's WNBA schedule...")

    try:
        games = get_home_teams_tm(today)
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

    LAUNCH_STAGGER_SEC = 90
    print(f"\n  Launching {len(eligible)} game runner(s) ({LAUNCH_STAGGER_SEC}s apart)...\n")
    import time as _time
    procs = []
    for i, slug in enumerate(eligible):
        if i > 0:
            print(f"  Waiting {LAUNCH_STAGGER_SEC}s before next launch...")
            _time.sleep(LAUNCH_STAGGER_SEC)
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
        print("\n  Regenerating WNBA story pages...")
        all_slugs = list(WNBA_TEAMS.keys()) if len(succeeded) > 1 else succeeded
        for slug in all_slugs:
            result = subprocess.run([PYTHON, "generate_wnba_story.py", slug], capture_output=True, text=True)
            if result.returncode == 0:
                print(f"  [{slug}] story page updated")
            else:
                print(f"  [{slug}] story page error: {result.stderr.strip()}")

        teams_str = "-".join(sorted(succeeded))
        subprocess.run(["git", "add", "data/wnba/", "docs/"], check=True)
        commit = subprocess.run([
            "git", "commit", "-m", f"WNBA auto-update {today}: {teams_str}"
        ])
        if commit.returncode == 0:
            subprocess.run(["git", "pull", "--rebase"], check=False)
            result = subprocess.run(["git", "push"])
            print("  Pushed to GitHub." if result.returncode == 0 else "  git push failed.")
        else:
            print("  Nothing new to commit.")

    if failed:
        print(f"\n  Failed: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
