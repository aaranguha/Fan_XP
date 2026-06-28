# nfl_teams.py
#
# Config for all 32 NFL teams.
#   slug         — folder name under data/nfl/ and CLI argument
#   tm_keyword   — search term for Ticketmaster Discovery API
#   espn_tricode — abbreviation used in ESPN scoreboard API

NFL_TEAMS = {
    # AFC East
    "bills":        {"tm_keyword": "Buffalo Bills",              "espn_tricode": "BUF", "city": "Orchard Park"},
    "dolphins":     {"tm_keyword": "Miami Dolphins",             "espn_tricode": "MIA", "city": "Miami Gardens"},
    "patriots":     {"tm_keyword": "New England Patriots",       "espn_tricode": "NE",  "city": "Foxborough"},
    "jets":         {"tm_keyword": "New York Jets",              "espn_tricode": "NYJ", "city": "East Rutherford"},
    # AFC North
    "ravens":       {"tm_keyword": "Baltimore Ravens",           "espn_tricode": "BAL", "city": "Baltimore"},
    "bengals":      {"tm_keyword": "Cincinnati Bengals",         "espn_tricode": "CIN", "city": "Cincinnati"},
    "browns":       {"tm_keyword": "Cleveland Browns",           "espn_tricode": "CLE", "city": "Cleveland"},
    "steelers":     {"tm_keyword": "Pittsburgh Steelers",        "espn_tricode": "PIT", "city": "Pittsburgh"},
    # AFC South
    "texans":       {"tm_keyword": "Houston Texans",             "espn_tricode": "HOU", "city": "Houston"},
    "colts":        {"tm_keyword": "Indianapolis Colts",         "espn_tricode": "IND", "city": "Indianapolis"},
    "jaguars":      {"tm_keyword": "Jacksonville Jaguars",       "espn_tricode": "JAX", "city": "Jacksonville"},
    "titans":       {"tm_keyword": "Tennessee Titans",           "espn_tricode": "TEN", "city": "Nashville"},
    # AFC West
    "broncos":      {"tm_keyword": "Denver Broncos",             "espn_tricode": "DEN", "city": "Denver"},
    "chiefs":       {"tm_keyword": "Kansas City Chiefs",         "espn_tricode": "KC",  "city": "Kansas City"},
    "raiders":      {"tm_keyword": "Las Vegas Raiders",          "espn_tricode": "LV",  "city": "Las Vegas"},
    "chargers":     {"tm_keyword": "Los Angeles Chargers",       "espn_tricode": "LAC", "city": "Inglewood"},
    # NFC East
    "cowboys":      {"tm_keyword": "Dallas Cowboys",             "espn_tricode": "DAL", "city": "Arlington"},
    "giants":       {"tm_keyword": "New York Giants",            "espn_tricode": "NYG", "city": "East Rutherford"},
    "eagles":       {"tm_keyword": "Philadelphia Eagles",        "espn_tricode": "PHI", "city": "Philadelphia"},
    "commanders":   {"tm_keyword": "Washington Commanders",      "espn_tricode": "WSH", "city": "Landover"},
    # NFC North
    "bears":        {"tm_keyword": "Chicago Bears",              "espn_tricode": "CHI", "city": "Chicago"},
    "lions":        {"tm_keyword": "Detroit Lions",              "espn_tricode": "DET", "city": "Detroit"},
    "packers":      {"tm_keyword": "Green Bay Packers",          "espn_tricode": "GB",  "city": "Green Bay"},
    "vikings":      {"tm_keyword": "Minnesota Vikings",          "espn_tricode": "MIN", "city": "Minneapolis"},
    # NFC South
    "falcons":      {"tm_keyword": "Atlanta Falcons",            "espn_tricode": "ATL", "city": "Atlanta"},
    "panthers":     {"tm_keyword": "Carolina Panthers",          "espn_tricode": "CAR", "city": "Charlotte"},
    "saints":       {"tm_keyword": "New Orleans Saints",         "espn_tricode": "NO",  "city": "New Orleans"},
    "buccaneers":   {"tm_keyword": "Tampa Bay Buccaneers",       "espn_tricode": "TB",  "city": "Tampa"},
    # NFC West
    "cardinals":    {"tm_keyword": "Arizona Cardinals",          "espn_tricode": "ARI", "city": "Glendale"},
    "rams":         {"tm_keyword": "Los Angeles Rams",           "espn_tricode": "LAR", "city": "Inglewood"},
    "49ers":        {"tm_keyword": "San Francisco 49ers",        "espn_tricode": "SF",  "city": "Santa Clara"},
    "seahawks":     {"tm_keyword": "Seattle Seahawks",           "espn_tricode": "SEA", "city": "Seattle"},
}

NFL_TRICODE_TO_SLUG = {v["espn_tricode"]: k for k, v in NFL_TEAMS.items()}


def get_nfl_team(slug: str) -> dict:
    slug = slug.lower()
    if slug not in NFL_TEAMS:
        valid = ", ".join(sorted(NFL_TEAMS.keys()))
        raise ValueError(f"Unknown NFL team '{slug}'. Valid options:\n  {valid}")
    return {"slug": slug, **NFL_TEAMS[slug]}


def nfl_game_dir(slug: str, game_date: str, opponent: str) -> str:
    import re
    opp_slug = re.sub(r"[^a-z0-9]+", "_", opponent.lower()).strip("_")
    return f"data/nfl/{slug}/{game_date}_{opp_slug}_at_{slug}"
