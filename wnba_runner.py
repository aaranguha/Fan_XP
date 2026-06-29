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
from datetime import datetime

import requests

from wnba_teams import WNBA_TEAMS, ESPN_ABBR_TO_SLUG

PYTHON = sys.executable


def get_home_teams_espn(today: str) -> list[str]:
    date_str = today.replace("-", "")
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={date_str}"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()

    slugs = []
    for event in resp.json().get("events", []):
        comp = event["competitions"][0]
        home = next((t for t in comp["competitors"] if t["homeAway"] == "home"), None)
        away = next((t for t in comp["competitors"] if t["homeAway"] == "away"), None)
        if not home:
            continue
        abbr = home["team"]["abbreviation"]
        slug = ESPN_ABBR_TO_SLUG.get(abbr)
        if slug:
            home_name = home["team"]["displayName"]
            away_name = away["team"]["displayName"] if away else "?"
            print(f"  Home game: {away_name} at {home_name}  →  {slug}")
            slugs.append(slug)
        else:
            print(f"  Unknown ESPN abbreviation: {abbr} — skipping")
    return slugs


def main():
    if len(sys.argv) > 1:
        today = sys.argv[1]
    else:
        today = os.getenv("WNBA_DATE") or datetime.now().strftime("%Y-%m-%d")
    print(f"[{today}] Checking today's WNBA schedule...")

    try:
        home_slugs = get_home_teams_espn(today)
    except Exception as e:
        print(f"Error fetching WNBA schedule: {e}")
        sys.exit(1)

    if not home_slugs:
        print("  No WNBA home games today.")
        sys.exit(0)

    print(f"\n  Launching {len(home_slugs)} game runner(s)...\n")
    procs = []
    for slug in home_slugs:
        log_dir = f"data/wnba/{slug}"
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
    results = []
    for slug, proc, log_file in procs:
        proc.wait()
        log_file.close()
        results.append((slug, proc.returncode))

    succeeded = [s for s, rc in results if rc == 0]
    failed    = [s for s, rc in results if rc != 0]

    if succeeded:
        teams_str = "-".join(sorted(succeeded))
        date_str  = today
        subprocess.run(["git", "add", "data/wnba/", "docs/"], check=True)
        commit = subprocess.run([
            "git", "commit", "-m", f"WNBA auto-update {date_str}: {teams_str}"
        ])
        if commit.returncode == 0:
            result = subprocess.run(["git", "push"])
            if result.returncode == 0:
                print("  Pushed to GitHub.")
            else:
                print("  git push failed.")
        else:
            print("  Nothing new to commit.")

    if failed:
        print(f"\n  Failed: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
