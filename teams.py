# teams.py
#
# Config for all 30 NBA teams.
#   slug       — folder name under data/ and CLI argument
#   tm_keyword — search term for Ticketmaster Discovery API
#   nba_city   — teamCity as it appears in NBA live scoreboard data


# ── Team draw ratings (2025-26 season) ────────────────────────────────────────
#
# Each team has two static scores (1-10):
#
#   star_power  — biggest name(s) on the roster.  Drives casual fan curiosity.
#                 10 = global icon (LeBron, Steph)
#                  9 = generational star (Wemby, Shai, Giannis, Jokic)
#                  8 = marquee All-Star (Tatum, KD, Brunson)
#                  6-7 = rising star or solid draw (Haliburton, Morant)
#                  4-5 = no household name, but competitive
#                  2-3 = rebuilding, no clear star
#
#   market_size — size of the TV/metro market.  Drives baseline demand.
#                 10 = NY, LA    9 = Chicago, Bay Area, Miami
#                  8 = Boston, Dallas, Houston, Phoenix, Toronto
#                  7 = Denver, Minneapolis, Portland, Atlanta, Detroit
#                  6 = Indy, Memphis, Milwaukee, OKC, Orlando, Philly, SA, Utah
#                  5 = Charlotte, Cleveland, New Orleans, OKC, Sacramento
#
# draw_score = (star_power * 0.5) + (market_size * 0.3) + (win_pct_score * 0.2)
# where win_pct_score = opponent_win_pct * 10  (added at runtime)

TEAM_DRAW = {
    # slug          star  market  key player(s)
    "lakers":     (10,   10),   # LeBron James — biggest brand in NBA
    "warriors":   (10,    9),   # Steph Curry — global icon, dynasty legacy
    "spurs":      ( 9,    6),   # Victor Wembanyama — generational talent
    "thunder":    ( 9,    6),   # Shai Gilgeous-Alexander — MVP candidate
    "celtics":    ( 8,    8),   # Tatum/Brown — reigning champs
    "bucks":      ( 9,    6),   # Giannis Antetokounmpo
    "nuggets":    ( 9,    7),   # Nikola Jokic — 3x MVP
    "knicks":     ( 8,   10),   # Brunson + Madison Square Garden premium
    "heat":       ( 7,    9),   # Miami market + culture
    "76ers":      ( 7,    8),   # Embiid (when healthy)
    "mavericks":  ( 7,    8),   # Post-Luka rebuild + Dallas market
    "suns":       ( 7,    8),   # Phoenix market
    "clippers":   ( 6,   10),   # LA market — star TBD
    "rockets":    ( 7,    8),   # Jalen Green ascending, Houston market
    "hawks":      ( 7,    7),   # Trae Young
    "grizzlies":  ( 7,    6),   # Ja Morant — electric when healthy
    "pacers":     ( 7,    6),   # Tyrese Haliburton, rising team
    "cavaliers":  ( 7,    7),   # Mitchel/Garland, strong team
    "timberwolves":(7,    7),   # Edwards — ascending star
    "bulls":      ( 5,    9),   # Chicago market carries it
    "nets":       ( 3,   10),   # Rebuilding, but Brooklyn/NY market
    "kings":      ( 6,    5),   # De'Aaron Fox / Sabonis
    "pelicans":   ( 6,    5),   # Zion (when healthy)
    "magic":      ( 6,    6),   # Franz Wagner, young exciting team
    "raptors":    ( 5,    8),   # Toronto market, rebuilding
    "hornets":    ( 5,    5),   # Rebuilding
    "wizards":    ( 3,    7),   # DC market, rebuilding
    "pistons":    ( 4,    7),   # Detroit market, rebuilding
    "jazz":       ( 4,    6),   # Rebuilding
    "blazers":    ( 4,    7),   # Rebuilding
}


def team_draw_score(slug: str, win_pct: float = 0.5) -> float:
    """
    Compute a team's draw score (1-10) given their slug and current win %.
    draw = (star * 0.5) + (market * 0.3) + (win_pct * 10 * 0.2)
    """
    star, market = TEAM_DRAW.get(slug, (5, 5))
    form = min(win_pct * 10, 10)
    return round(star * 0.5 + market * 0.3 + form * 0.2, 2)


def slug_from_fullname(fullname: str) -> str | None:
    """Best-effort reverse lookup: 'Los Angeles Lakers' → 'lakers'."""
    fullname_lower = fullname.lower()
    for slug, cfg in TEAMS.items():
        if cfg["tm_keyword"].lower() == fullname_lower:
            return slug
    # Fallback: match on last word (nickname)
    nickname = fullname_lower.split()[-1]
    for slug in TEAMS:
        if slug == nickname or slug.rstrip("s") == nickname.rstrip("s"):
            return slug
    return None

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
    # TM uses "vs.", "vs", or "v." depending on the event
    if " vs. " in event_name:
        sep = " vs. "
    elif " v. " in event_name:
        sep = " v. "
    else:
        sep = " vs "
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


def pre_seats_csv(gdir: str) -> str:
    return f"{gdir}/pre_game_seats.csv"


def halftime_seats_csv(gdir: str) -> str:
    return f"{gdir}/halftime_seats.csv"


def no_shows_csv(gdir: str) -> str:
    return f"{gdir}/no_shows.csv"
