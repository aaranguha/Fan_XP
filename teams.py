# teams.py
#
# Config for all 30 NBA teams.
#   slug       — folder name under data/ and CLI argument
#   tm_keyword — search term for Ticketmaster Discovery API
#   nba_city   — teamCity as it appears in NBA live scoreboard data

TEAMS = {
    "hawks":        {"tm_keyword": "Atlanta Hawks",         "nba_city": "Atlanta"},
    "celtics":      {"tm_keyword": "Boston Celtics",        "nba_city": "Boston"},
    "nets":         {"tm_keyword": "Brooklyn Nets",         "nba_city": "Brooklyn"},
    "hornets":      {"tm_keyword": "Charlotte Hornets",     "nba_city": "Charlotte"},
    "bulls":        {"tm_keyword": "Chicago Bulls",         "nba_city": "Chicago"},
    "cavaliers":    {"tm_keyword": "Cleveland Cavaliers",   "nba_city": "Cleveland"},
    "mavericks":    {"tm_keyword": "Dallas Mavericks",      "nba_city": "Dallas"},
    "nuggets":      {"tm_keyword": "Denver Nuggets",        "nba_city": "Denver"},
    "pistons":      {"tm_keyword": "Detroit Pistons",       "nba_city": "Detroit"},
    "warriors":     {"tm_keyword": "Golden State Warriors", "nba_city": "Golden State"},
    "rockets":      {"tm_keyword": "Houston Rockets",       "nba_city": "Houston"},
    "pacers":       {"tm_keyword": "Indiana Pacers",        "nba_city": "Indiana"},
    "clippers":     {"tm_keyword": "LA Clippers",           "nba_city": "LA"},
    "lakers":       {"tm_keyword": "Los Angeles Lakers",    "nba_city": "Los Angeles"},
    "grizzlies":    {"tm_keyword": "Memphis Grizzlies",     "nba_city": "Memphis"},
    "heat":         {"tm_keyword": "Miami Heat",            "nba_city": "Miami"},
    "bucks":        {"tm_keyword": "Milwaukee Bucks",       "nba_city": "Milwaukee"},
    "timberwolves": {"tm_keyword": "Minnesota Timberwolves","nba_city": "Minnesota"},
    "pelicans":     {"tm_keyword": "New Orleans Pelicans",  "nba_city": "New Orleans"},
    "knicks":       {"tm_keyword": "New York Knicks",       "nba_city": "New York"},
    "thunder":      {"tm_keyword": "Oklahoma City Thunder", "nba_city": "Oklahoma City"},
    "magic":        {"tm_keyword": "Orlando Magic",         "nba_city": "Orlando"},
    "76ers":        {"tm_keyword": "Philadelphia 76ers",    "nba_city": "Philadelphia"},
    "suns":         {"tm_keyword": "Phoenix Suns",          "nba_city": "Phoenix"},
    "blazers":      {"tm_keyword": "Portland Trail Blazers","nba_city": "Portland"},
    "kings":        {"tm_keyword": "Sacramento Kings",      "nba_city": "Sacramento"},
    "spurs":        {"tm_keyword": "San Antonio Spurs",     "nba_city": "San Antonio"},
    "raptors":      {"tm_keyword": "Toronto Raptors",       "nba_city": "Toronto"},
    "jazz":         {"tm_keyword": "Utah Jazz",             "nba_city": "Utah"},
    "wizards":      {"tm_keyword": "Washington Wizards",    "nba_city": "Washington"},
}


def get_team(slug: str) -> dict:
    """Return team config for a slug, or raise a clear error."""
    slug = slug.lower()
    if slug not in TEAMS:
        valid = ", ".join(sorted(TEAMS.keys()))
        raise ValueError(f"Unknown team '{slug}'. Valid options:\n  {valid}")
    return {"slug": slug, **TEAMS[slug]}


def data_dir(slug: str) -> str:
    """Return the team-level data folder (e.g. 'data/warriors')."""
    return f"data/{slug}"


def game_dir(team_slug: str, game_date: str, event_name: str) -> str:
    """
    Return the per-game folder path.
    e.g. data/magic/2026-03-11_cleveland_cavaliers_at_magic

    event_name format: "Orlando Magic vs. Cleveland Cavaliers"
    """
    import re
    # TM uses "vs." or "vs" depending on the event
    sep = " vs. " if " vs. " in event_name else " vs "
    parts = event_name.split(sep, 1)
    if len(parts) == 2:
        opponent      = parts[1].strip()
        opponent_slug = re.sub(r"[^a-z0-9]+", "_", opponent.lower()).strip("_")
        folder        = f"{game_date}_{opponent_slug}_at_{team_slug}"
    else:
        folder = game_date
    return f"data/{team_slug}/{folder}"


def pre_game_csv(gdir: str) -> str:
    return f"{gdir}/pre_game.csv"


def halftime_csv(gdir: str) -> str:
    return f"{gdir}/halftime.csv"


def no_shows_csv(gdir: str) -> str:
    return f"{gdir}/no_shows.csv"
