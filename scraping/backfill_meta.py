"""
backfill_meta.py

Generate game_meta.json for every existing game folder that doesn't have one.
Parses game date, home team, and opponent from the folder name.
Opponent win/loss record is looked up from NBA API historical standings.

Usage:
    python backfill_meta.py
"""

import json
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from teams import team_draw_score, slug_from_fullname

DATA_DIR = "data"

# Map team slug → nba_api TeamCity (for standings lookup)
SLUG_TO_CITY = {
    "hawks": "Atlanta",       "celtics": "Boston",       "nets": "Brooklyn",
    "hornets": "Charlotte",   "bulls": "Chicago",        "cavaliers": "Cleveland",
    "mavericks": "Dallas",    "nuggets": "Denver",       "pistons": "Detroit",
    "warriors": "Golden State","rockets": "Houston",     "pacers": "Indiana",
    "clippers": "LA",         "lakers": "Los Angeles",   "grizzlies": "Memphis",
    "heat": "Miami",          "bucks": "Milwaukee",      "timberwolves": "Minnesota",
    "pelicans": "New Orleans", "knicks": "New York",     "thunder": "Oklahoma City",
    "magic": "Orlando",       "76ers": "Philadelphia",   "suns": "Phoenix",
    "blazers": "Portland",    "kings": "Sacramento",     "spurs": "San Antonio",
    "raptors": "Toronto",     "jazz": "Utah",            "wizards": "Washington",
}

ARENA_BY_SLUG = {
    "hawks": "State Farm Arena",          "celtics": "TD Garden",
    "nets": "Barclays Center",            "hornets": "Spectrum Center",
    "bulls": "United Center",             "cavaliers": "Rocket Mortgage FieldHouse",
    "mavericks": "American Airlines Center","nuggets": "Ball Arena",
    "pistons": "Little Caesars Arena",    "warriors": "Chase Center",
    "rockets": "Toyota Center",           "pacers": "Gainbridge Fieldhouse",
    "clippers": "Intuit Dome",            "lakers": "Crypto.com Arena",
    "grizzlies": "FedExForum",            "heat": "Kaseya Center",
    "bucks": "Fiserv Forum",              "timberwolves": "Target Center",
    "pelicans": "Smoothie King Center",   "knicks": "Madison Square Garden",
    "thunder": "Paycom Center",           "magic": "Kia Center",
    "76ers": "Wells Fargo Center",        "suns": "Footprint Center",
    "blazers": "Moda Center",             "kings": "Golden 1 Center",
    "spurs": "Frost Bank Center",         "raptors": "Scotiabank Arena",
    "jazz": "Delta Center",               "wizards": "Capital One Arena",
}

CITY_BY_SLUG = {
    "hawks": "Atlanta",         "celtics": "Boston",        "nets": "Brooklyn",
    "hornets": "Charlotte",     "bulls": "Chicago",         "cavaliers": "Cleveland",
    "mavericks": "Dallas",      "nuggets": "Denver",        "pistons": "Detroit",
    "warriors": "San Francisco","rockets": "Houston",       "pacers": "Indianapolis",
    "clippers": "Inglewood",    "lakers": "Los Angeles",    "grizzlies": "Memphis",
    "heat": "Miami",            "bucks": "Milwaukee",       "timberwolves": "Minneapolis",
    "pelicans": "New Orleans",  "knicks": "New York",       "thunder": "Oklahoma City",
    "magic": "Orlando",         "76ers": "Philadelphia",    "suns": "Phoenix",
    "blazers": "Portland",      "kings": "Sacramento",      "spurs": "San Antonio",
    "raptors": "Toronto",       "jazz": "Salt Lake City",   "wizards": "Washington",
}

# Slug-to-full-team-name for opponent matching
SLUG_TO_FULLNAME = {
    "hawks": "Atlanta Hawks",         "celtics": "Boston Celtics",
    "nets": "Brooklyn Nets",          "hornets": "Charlotte Hornets",
    "bulls": "Chicago Bulls",         "cavaliers": "Cleveland Cavaliers",
    "mavericks": "Dallas Mavericks",  "nuggets": "Denver Nuggets",
    "pistons": "Detroit Pistons",     "warriors": "Golden State Warriors",
    "rockets": "Houston Rockets",     "pacers": "Indiana Pacers",
    "clippers": "LA Clippers",        "lakers": "Los Angeles Lakers",
    "grizzlies": "Memphis Grizzlies", "heat": "Miami Heat",
    "bucks": "Milwaukee Bucks",       "timberwolves": "Minnesota Timberwolves",
    "pelicans": "New Orleans Pelicans","knicks": "New York Knicks",
    "thunder": "Oklahoma City Thunder","magic": "Orlando Magic",
    "76ers": "Philadelphia 76ers",    "suns": "Phoenix Suns",
    "blazers": "Portland Trail Blazers","kings": "Sacramento Kings",
    "spurs": "San Antonio Spurs",     "raptors": "Toronto Raptors",
    "jazz": "Utah Jazz",              "wizards": "Washington Wizards",
}


def slug_to_opponent_name(opponent_slug: str) -> str:
    return SLUG_TO_FULLNAME.get(opponent_slug, opponent_slug.replace("_", " ").title())


def parse_folder(team_slug: str, folder: str):
    """
    Parse a game folder name into (game_date, opponent_name).
    Format: YYYY-MM-DD_opponent_slug_at_team_slug
    e.g.  2026-03-25_los_angeles_lakers_at_pacers
    """
    m = re.match(r"^(\d{4}-\d{2}-\d{2})_(.+)_at_(.+)$", folder)
    if not m:
        return None, None
    game_date    = m.group(1)
    opponent_raw = m.group(2)   # e.g. "los_angeles_lakers"
    opponent_name = slug_to_opponent_name(opponent_raw)
    return game_date, opponent_name


def get_opponent_record(opponent_name: str) -> dict:
    try:
        from nba_api.stats.endpoints import leaguestandings
        df = leaguestandings.LeagueStandings().get_data_frames()[0]
        df["FullName"] = df["TeamCity"] + " " + df["TeamName"]
        match = df[df["FullName"].str.lower() == opponent_name.lower()]
        if match.empty:
            nickname = opponent_name.split()[-1].lower()
            match = df[df["TeamName"].str.lower() == nickname]
        if not match.empty:
            row   = match.iloc[0]
            wins   = int(row.get("WINS", row.get("W", 0)))
            losses = int(row.get("LOSSES", row.get("L", 0)))
            total  = wins + losses
            return {
                "opponent_wins":    wins,
                "opponent_losses":  losses,
                "opponent_win_pct": round(wins / total, 3) if total else 0.0,
            }
    except Exception:
        pass
    return {}


def backfill():
    # Pull standings once for all teams
    standings_cache = {}
    try:
        from nba_api.stats.endpoints import leaguestandings
        df = leaguestandings.LeagueStandings().get_data_frames()[0]
        df["FullName"] = df["TeamCity"] + " " + df["TeamName"]
        for _, row in df.iterrows():
            name   = row["FullName"].lower()
            wins   = int(row.get("WINS", row.get("W", 0)))
            losses = int(row.get("LOSSES", row.get("L", 0)))
            total  = wins + losses
            standings_cache[name] = {
                "opponent_wins":    wins,
                "opponent_losses":  losses,
                "opponent_win_pct": round(wins / total, 3) if total else 0.0,
            }
        print(f"Loaded standings for {len(standings_cache)} teams.")
    except Exception as e:
        print(f"Warning: could not load NBA standings ({e}) — opponent records will be omitted.")

    saved = skipped = 0

    for team_slug in sorted(os.listdir(DATA_DIR)):
        team_dir = os.path.join(DATA_DIR, team_slug)
        if not os.path.isdir(team_dir):
            continue

        for folder in sorted(os.listdir(team_dir)):
            gdir = os.path.join(team_dir, folder)
            if not os.path.isdir(gdir):
                continue

            meta_path = os.path.join(gdir, "game_meta.json")
            if os.path.isfile(meta_path):
                skipped += 1
                continue

            # Only process folders that look like game folders
            game_date, opponent_name = parse_folder(team_slug, folder)
            if not game_date:
                continue

            day_of_week = datetime.strptime(game_date, "%Y-%m-%d").strftime("%A")

            # Look up opponent record from standings cache
            opp_lower = opponent_name.lower()
            record = standings_cache.get(opp_lower, {})
            if not record:
                nickname = opponent_name.split()[-1].lower()
                for k, v in standings_cache.items():
                    if k.endswith(nickname):
                        record = v
                        break

            opp_win_pct  = record.get("opponent_win_pct", 0.5)
            opp_slug     = slug_from_fullname(opponent_name)
            home_draw    = team_draw_score(team_slug)
            opp_draw     = team_draw_score(opp_slug, opp_win_pct) if opp_slug else round(opp_win_pct * 10 * 0.2 + 5 * 0.8, 2)
            game_appeal  = round((home_draw + opp_draw) / 2, 2)

            meta = {
                "home_team":           team_slug,
                "opponent":            opponent_name,
                "game_date":           game_date,
                "day_of_week":         day_of_week,
                "tipoff_local":        "",
                "arena":               ARENA_BY_SLUG.get(team_slug, ""),
                "city":                CITY_BY_SLUG.get(team_slug, ""),
                "home_draw_score":     home_draw,
                "opponent_draw_score": opp_draw,
                "game_appeal_score":   game_appeal,
            }
            meta.update(record)

            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)

            print(f"  Saved: {gdir}/game_meta.json")
            saved += 1

    print(f"\nDone. {saved} created, {skipped} already existed.")


if __name__ == "__main__":
    backfill()
