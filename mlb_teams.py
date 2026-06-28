# mlb_teams.py
#
# Config for all 30 MLB teams.
#   slug         — folder name under data/ and CLI argument
#   tm_keyword   — search term for Ticketmaster Discovery API
#   espn_tricode — abbreviation used in ESPN scoreboard API
#   mlb_team_id  — MLB Stats API team ID (for live game polling)
#   nba_city     — teamCity field (reused field name; here it's the MLB city)

MLB_TEAMS = {
    "diamondbacks": {"tm_keyword": "Arizona Diamondbacks",     "espn_tricode": "ARI", "mlb_team_id": 109, "city": "Phoenix"},
    "braves":       {"tm_keyword": "Atlanta Braves",           "espn_tricode": "ATL", "mlb_team_id": 144, "city": "Atlanta"},
    "orioles":      {"tm_keyword": "Baltimore Orioles",        "espn_tricode": "BAL", "mlb_team_id": 110, "city": "Baltimore"},
    "redsox":       {"tm_keyword": "Boston Red Sox",           "espn_tricode": "BOS", "mlb_team_id": 111, "city": "Boston"},
    "cubs":         {"tm_keyword": "Chicago Cubs",             "espn_tricode": "CHC", "mlb_team_id": 112, "city": "Chicago"},
    "whitesox":     {"tm_keyword": "Chicago White Sox",        "espn_tricode": "CHW", "mlb_team_id": 145, "city": "Chicago"},
    "reds":         {"tm_keyword": "Cincinnati Reds",          "espn_tricode": "CIN", "mlb_team_id": 113, "city": "Cincinnati"},
    "guardians":    {"tm_keyword": "Cleveland Guardians",      "espn_tricode": "CLE", "mlb_team_id": 114, "city": "Cleveland"},
    "rockies":      {"tm_keyword": "Colorado Rockies",         "espn_tricode": "COL", "mlb_team_id": 115, "city": "Denver"},
    "tigers":       {"tm_keyword": "Detroit Tigers",           "espn_tricode": "DET", "mlb_team_id": 116, "city": "Detroit"},
    "astros":       {"tm_keyword": "Houston Astros",           "espn_tricode": "HOU", "mlb_team_id": 117, "city": "Houston"},
    "royals":       {"tm_keyword": "Kansas City Royals",       "espn_tricode": "KC",  "mlb_team_id": 118, "city": "Kansas City"},
    "angels":       {"tm_keyword": "Los Angeles Angels",       "espn_tricode": "LAA", "mlb_team_id": 108, "city": "Anaheim"},
    "dodgers":      {"tm_keyword": "Los Angeles Dodgers",      "espn_tricode": "LAD", "mlb_team_id": 119, "city": "Los Angeles"},
    "marlins":      {"tm_keyword": "Miami Marlins",            "espn_tricode": "MIA", "mlb_team_id": 146, "city": "Miami"},
    "brewers":      {"tm_keyword": "Milwaukee Brewers",        "espn_tricode": "MIL", "mlb_team_id": 158, "city": "Milwaukee"},
    "twins":        {"tm_keyword": "Minnesota Twins",          "espn_tricode": "MIN", "mlb_team_id": 142, "city": "Minneapolis"},
    "mets":         {"tm_keyword": "New York Mets",            "espn_tricode": "NYM", "mlb_team_id": 121, "city": "New York"},
    "yankees":      {"tm_keyword": "New York Yankees",         "espn_tricode": "NYY", "mlb_team_id": 147, "city": "New York"},
    "athletics":    {"tm_keyword": "Athletics",                "espn_tricode": "OAK", "mlb_team_id": 133, "city": "Sacramento"},
    "phillies":     {"tm_keyword": "Philadelphia Phillies",    "espn_tricode": "PHI", "mlb_team_id": 143, "city": "Philadelphia"},
    "pirates":      {"tm_keyword": "Pittsburgh Pirates",       "espn_tricode": "PIT", "mlb_team_id": 134, "city": "Pittsburgh"},
    "padres":       {"tm_keyword": "San Diego Padres",         "espn_tricode": "SD",  "mlb_team_id": 135, "city": "San Diego"},
    "giants":       {"tm_keyword": "San Francisco Giants",     "espn_tricode": "SF",  "mlb_team_id": 137, "city": "San Francisco"},
    "mariners":     {"tm_keyword": "Seattle Mariners",         "espn_tricode": "SEA", "mlb_team_id": 136, "city": "Seattle"},
    "cardinals":    {"tm_keyword": "St. Louis Cardinals",      "espn_tricode": "STL", "mlb_team_id": 138, "city": "St. Louis"},
    "rays":         {"tm_keyword": "Tampa Bay Rays",           "espn_tricode": "TB",  "mlb_team_id": 139, "city": "St. Petersburg"},
    "rangers":      {"tm_keyword": "Texas Rangers",            "espn_tricode": "TEX", "mlb_team_id": 140, "city": "Arlington"},
    "bluejays":     {"tm_keyword": "Toronto Blue Jays",        "espn_tricode": "TOR", "mlb_team_id": 141, "city": "Toronto"},
    "nationals":    {"tm_keyword": "Washington Nationals",     "espn_tricode": "WSH", "mlb_team_id": 120, "city": "Washington"},
}

# ESPN tricode → slug (mirrors NBA_TRICODE_TO_SLUG in daily_runner.py)
MLB_TRICODE_TO_SLUG = {v["espn_tricode"]: k for k, v in MLB_TEAMS.items()}


def get_mlb_team(slug: str) -> dict:
    slug = slug.lower()
    if slug not in MLB_TEAMS:
        valid = ", ".join(sorted(MLB_TEAMS.keys()))
        raise ValueError(f"Unknown MLB team '{slug}'. Valid options:\n  {valid}")
    return {"slug": slug, **MLB_TEAMS[slug]}


def mlb_data_dir(slug: str) -> str:
    return f"data/mlb/{slug}"


def mlb_game_dir(slug: str, game_date: str, opponent: str) -> str:
    import re
    opp_slug = re.sub(r"[^a-z0-9]+", "_", opponent.lower()).strip("_")
    return f"data/mlb/{slug}/{game_date}_{opp_slug}_at_{slug}"
