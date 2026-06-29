"""WNBA team configs for the 2025-26 season (13 teams)."""

WNBA_TEAMS = {
    "dream":      {"tm_keyword": "Atlanta Dream",           "espn_abbr": "ATL"},
    "sky":        {"tm_keyword": "Chicago Sky",             "espn_abbr": "CHI"},
    "sun":        {"tm_keyword": "Connecticut Sun",         "espn_abbr": "CONN"},
    "wings":      {"tm_keyword": "Dallas Wings",            "espn_abbr": "DAL"},
    "valkyries":  {"tm_keyword": "Golden State Valkyries",  "espn_abbr": "GS"},
    "fever":      {"tm_keyword": "Indiana Fever",           "espn_abbr": "IND"},
    "aces":       {"tm_keyword": "Las Vegas Aces",          "espn_abbr": "LV"},
    "sparks":     {"tm_keyword": "Los Angeles Sparks",      "espn_abbr": "LA"},
    "lynx":       {"tm_keyword": "Minnesota Lynx",          "espn_abbr": "MIN"},
    "liberty":    {"tm_keyword": "New York Liberty",        "espn_abbr": "NY"},
    "mercury":    {"tm_keyword": "Phoenix Mercury",         "espn_abbr": "PHX"},
    "storm":      {"tm_keyword": "Seattle Storm",           "espn_abbr": "SEA"},
    "mystics":    {"tm_keyword": "Washington Mystics",      "espn_abbr": "WSH"},
}

ESPN_ABBR_TO_SLUG = {v["espn_abbr"]: k for k, v in WNBA_TEAMS.items()}


def get_wnba_team(slug: str) -> dict:
    slug = slug.lower()
    if slug not in WNBA_TEAMS:
        valid = ", ".join(sorted(WNBA_TEAMS.keys()))
        raise ValueError(f"Unknown WNBA team '{slug}'. Valid options:\n  {valid}")
    return {"slug": slug, **WNBA_TEAMS[slug]}


def wnba_game_dir(team_slug: str, game_date: str, opponent: str) -> str:
    import re
    opp_slug = re.sub(r"[^a-z0-9]+", "_", opponent.lower()).strip("_")
    return f"data/wnba/{team_slug}/{game_date}_{opp_slug}_at_{team_slug}"
