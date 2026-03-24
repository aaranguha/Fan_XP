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
import shutil
import subprocess
import sys
import time
from datetime import datetime

import requests
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


def get_home_teams_today(today: str) -> list[str]:
    """
    Return slugs for all teams with a home game today.
    Uses NBA CDN season schedule — returns the full day's slate at any time of day,
    unlike ScoreboardV3 which only shows games that are live/near-live.
    """
    url  = "https://cdn.nba.com/static/json/staticData/scheduleLeagueV2_1.json"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()

    # CDN dates are formatted "MM/DD/YYYY HH:MM:SS"
    date_prefix = datetime.strptime(today, "%Y-%m-%d").strftime("%m/%d/%Y")
    game_dates  = resp.json()["leagueSchedule"]["gameDates"]

    slugs = []
    for gd in game_dates:
        if not gd["gameDate"].startswith(date_prefix):
            continue
        for game in gd["games"]:
            tricode   = game.get("homeTeam", {}).get("teamTricode", "")
            slug      = NBA_TRICODE_TO_SLUG.get(tricode)
            if slug:
                home_city = game["homeTeam"]["teamCity"]
                away_city = game["awayTeam"]["teamCity"]
                print(f"  Home game found: {home_city} vs {away_city}  →  slug: {slug}")
                slugs.append(slug)
        break  # found today's date block

    return slugs


def launch_team(slug: str, today: str) -> subprocess.Popen:
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
        [PYTHON, "run_game.py", slug, today],
        stdout=log_file,
        stderr=log_file,
    )
    return proc, log_file


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[{today}] Checking today's NBA schedule...")

    try:
        slugs = get_home_teams_today(today)
    except Exception as e:
        print(f"Error fetching schedule: {e}")
        sys.exit(1)

    if not slugs:
        print("No home games today.")
        return

    # Priority teams launch first
    PRIORITY = ["warriors"]
    slugs = sorted(slugs, key=lambda s: (0 if s in PRIORITY else 1, s))

    print(f"\nLaunching {len(slugs)} game runner(s)...\n")

    procs = []
    for i, slug in enumerate(slugs):
        if i > 0:
            time.sleep(5)   # stagger launches to avoid TM API rate limit (429)
        proc, log_file = launch_team(slug, today)
        procs.append((slug, proc, log_file))
        print(f"  [{slug}] PID {proc.pid}  →  data/{slug}/game.log")

    print(f"\nAll {len(procs)} runner(s) started. Waiting for games to complete...")
    print("(Each runner sleeps until its own game time — this may take many hours)\n")

    for slug, proc, log_file in procs:
        proc.wait()
        log_file.close()
        if proc.returncode == 0:
            status = "done"
        elif proc.returncode == 2:
            status = "FAILED (Bot Detection)"
        else:
            status = f"FAILED (exit {proc.returncode})"
        print(f"  [{slug}] {status}")

    # Remove empty game folders, then empty team folders
    for team_dir in os.listdir("data"):
        team_path = os.path.join("data", team_dir)
        if not os.path.isdir(team_path):
            continue
        for game_folder in os.listdir(team_path):
            game_path = os.path.join(team_path, game_folder)
            if not os.path.isdir(game_path):
                continue
            csvs = [f for f in os.listdir(game_path) if f.endswith(".csv")]
            if not csvs:
                print(f"  Removing empty folder: {game_path}")
                shutil.rmtree(game_path, ignore_errors=True)
        # Remove team folder if it has no game data at all
        remaining = [f for f in os.listdir(team_path) if f != "game.log"]
        if not remaining:
            print(f"  Removing empty team folder: {team_path}")
            shutil.rmtree(team_path, ignore_errors=True)

    print("\nAll done. Check data/<team>/no_shows.csv for results.")


if __name__ == "__main__":
    main()
