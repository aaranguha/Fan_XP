"""
daily_runner.py

Checks today's NBA schedule and launches run_game.py for each team
with a home game today. All teams run in parallel as background processes,
each sleeping until their own game's pre_game and halftime windows.

Logs for each team are written to data/<team>/game.log.

Usage:
    python daily_runner.py

Cron setup (runs at 6 AM local time every day):
    crontab -e
    Add this line (update path as needed):
    0 6 * * * cd "/Users/aguha2021/Desktop/CS_Projects/Fan XP" && /opt/homebrew/Caskroom/miniconda/base/bin/python3 daily_runner.py >> data/daily_runner.log 2>&1
"""

import os
import subprocess
import sys
import time
from datetime import datetime

from nba_api.live.nba.endpoints import scoreboard as live_scoreboard
from teams import TEAMS, data_dir

PYTHON = sys.executable

# NBA team tricode → our slug (stable across seasons)
NBA_TRICODE_TO_SLUG = {
    "ATL": "hawks",       "BOS": "celtics",      "BKN": "nets",
    "CHA": "hornets",     "CHI": "bulls",        "CLE": "cavaliers",
    "DAL": "mavericks",   "DEN": "nuggets",      "DET": "pistons",
    "GSW": "warriors",    "HOU": "rockets",      "IND": "pacers",
    "LAC": "clippers",    "LAL": "lakers",       "MEM": "grizzlies",
    "MIA": "heat",        "MIL": "bucks",        "MIN": "timberwolves",
    "NOP": "pelicans",    "NYK": "knicks",       "OKC": "thunder",
    "ORL": "magic",       "PHI": "76ers",        "PHX": "suns",
    "POR": "blazers",     "SAC": "kings",        "SAS": "spurs",
    "TOR": "raptors",     "UTA": "jazz",         "WAS": "wizards",
}


def get_home_teams_today() -> list[str]:
    """
    Return slugs for all teams with a home game today.
    Uses nba_api live scoreboard — no TM API calls.
    """
    board = live_scoreboard.ScoreBoard()
    games = board.games.get_dict()

    slugs = []
    for game in games:
        tricode = game.get("homeTeam", {}).get("teamTricode", "")
        slug    = NBA_TRICODE_TO_SLUG.get(tricode)
        if slug:
            home_city = game.get("homeTeam", {}).get("teamCity", "")
            away_city = game.get("awayTeam", {}).get("teamCity", "")
            print(f"  Home game found: {home_city} vs {away_city}  →  slug: {slug}")
            slugs.append(slug)

    return slugs


def launch_team(slug: str) -> subprocess.Popen:
    """
    Launch run_game.py for a team as a background process.
    Stdout and stderr are appended to data/<slug>/game.log.
    """
    log_dir  = data_dir(slug)
    os.makedirs(log_dir, exist_ok=True)
    log_path = f"{log_dir}/game.log"

    log_file = open(log_path, "a")
    log_file.write(f"\n{'='*54}\n")
    log_file.write(f"  Run started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    log_file.write(f"{'='*54}\n")
    log_file.flush()

    proc = subprocess.Popen(
        [PYTHON, "run_game.py", slug],
        stdout=log_file,
        stderr=log_file,
    )
    return proc, log_file


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[{today}] Checking today's NBA schedule...")

    try:
        slugs = get_home_teams_today()
    except Exception as e:
        print(f"Error fetching schedule: {e}")
        sys.exit(1)

    if not slugs:
        print("No home games today.")
        return

    print(f"\nLaunching {len(slugs)} game runner(s)...\n")

    procs = []
    for i, slug in enumerate(slugs):
        if i > 0:
            time.sleep(5)   # stagger launches to avoid TM API rate limit (429)
        proc, log_file = launch_team(slug)
        procs.append((slug, proc, log_file))
        print(f"  [{slug}] PID {proc.pid}  →  data/{slug}/game.log")

    print(f"\nAll {len(procs)} runner(s) started. Waiting for games to complete...")
    print("(Each runner sleeps until its own game time — this may take many hours)\n")

    for slug, proc, log_file in procs:
        proc.wait()
        log_file.close()
        status = "done" if proc.returncode == 0 else f"FAILED (exit {proc.returncode})"
        print(f"  [{slug}] {status}")

    print("\nAll done. Check data/<team>/no_shows.csv for results.")


if __name__ == "__main__":
    main()
