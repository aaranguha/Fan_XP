"""
run_game.py

Automated game-day runner. Call once on game day and it handles everything:
  1. Look up the next home game for the given team (1 TM API call total).
  2. Sleep until 1hr before tip-off → scrape pre_game snapshot.
  3. Poll the live NBA game clock every 30s → scrape halftime snapshot when
     Q2 hits ≤2 min remaining (falls back to 52 min after tip-off if live
     data is unavailable).
  4. Run the no-show comparison and print the report.

Usage:
    python run_game.py <team_slug>

    e.g.  python run_game.py warriors
          python run_game.py lakers

Keep the terminal open. Your machine just needs to stay awake.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

from teams import get_team, game_dir, pre_game_csv, halftime_csv, no_shows_csv, data_dir
from fetch_listings import (
    find_next_home_game,
    scrape_listings,
    launch_browser_session,
    close_browser_session,
    parse_facet,
    save_csv,
    print_summary,
)
from compare_snapshots import (
    load_csv,
    compare,
    save_no_shows,
    print_report,
)


# ── Timing config ──────────────────────────────────────────────────────────────
PRE_GAME_OFFSET_MIN   = 60   # scrape this many minutes BEFORE tip-off
HALFTIME_FALLBACK_MIN = 52   # fallback offset AFTER tip-off if live clock unavailable
POLL_INTERVAL_SEC     = 30   # how often to check the live game clock
Q2_TRIGGER_MIN        = 2    # trigger halftime scrape when Q2 clock ≤ this many minutes
WARM_INTERVAL_MIN     = 25   # how often to refresh TM page during keep-alive wait

# Teams that keep the browser open between pre-game and halftime to avoid bot detection
KEEP_ALIVE_TEAMS = {"warriors"}


def get_tipoff_utc(event: dict) -> datetime:
    dt_str = event.get("dates", {}).get("start", {}).get("dateTime")
    if not dt_str:
        raise RuntimeError("Event has no dateTime field — cannot schedule automatically.")
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def sleep_until(target: datetime, label: str) -> None:
    now  = datetime.now(timezone.utc)
    wait = (target - now).total_seconds()
    if wait <= 0:
        print(f"  [{label}] Scheduled time already passed — running now.")
        return
    wake = target.strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"  Waiting until {wake} for {label} scrape ({wait / 60:.1f} min from now)...")
    time.sleep(wait)


def parse_clock_minutes(clock_str: str) -> float:
    """Parse NBA live clock 'PT02M34.56S' → total minutes as float."""
    match = re.match(r"PT(\d+)M([\d.]+)S", clock_str or "")
    if not match:
        return 99.0
    return int(match.group(1)) + float(match.group(2)) / 60


def find_team_game(games: list[dict], nba_city: str) -> dict | None:
    """Return the game dict for the given team from a live scoreboard games list."""
    for game in games:
        home = game.get("homeTeam", {}).get("teamCity", "")
        away = game.get("awayTeam", {}).get("teamCity", "")
        if nba_city in home or nba_city in away:
            return game
    return None


def warm_browser(page, event_url: str) -> None:
    """Refresh the TM event page to keep the browser session alive."""
    print(f"  [keep-alive] Refreshing TM page to warm session...")
    try:
        page.goto(event_url, wait_until="load", timeout=45000)
        page.wait_for_timeout(12000)
    except Exception as e:
        print(f"  [keep-alive] Warning: warm refresh failed: {e}")


def wait_for_halftime(tipoff: datetime, nba_city: str, warm_page=None, warm_url: str = "") -> None:
    """
    Poll NBA live scoreboard every 30s and return when Q2 has ≤2 min left.
    Falls back to a fixed offset if nba_api is unavailable or game not found.
    """
    try:
        from nba_api.live.nba.endpoints import scoreboard as nba_scoreboard
        live_available = True
    except ImportError:
        live_available = False

    fallback_time = tipoff + timedelta(minutes=HALFTIME_FALLBACK_MIN)

    if not live_available:
        print(f"  nba_api not installed — using fixed {HALFTIME_FALLBACK_MIN}min fallback.")
        sleep_until(fallback_time, "halftime")
        return

    print(f"  Polling live game clock every {POLL_INTERVAL_SEC}s "
          f"(fallback at {fallback_time.strftime('%H:%M UTC')})...")

    last_warm = datetime.now(timezone.utc)

    while True:
        elapsed = (datetime.now(timezone.utc) - tipoff).total_seconds() / 60
        if elapsed < 20:
            time.sleep(60)
            continue

        if datetime.now(timezone.utc) >= fallback_time:
            print("  Fallback deadline reached — triggering halftime scrape.")
            return

        # Periodically refresh TM page to keep browser session warm
        if warm_page and warm_url:
            since_warm = (datetime.now(timezone.utc) - last_warm).total_seconds() / 60
            if since_warm >= WARM_INTERVAL_MIN:
                warm_browser(warm_page, warm_url)
                last_warm = datetime.now(timezone.utc)

        try:
            board = nba_scoreboard.ScoreBoard()
            game  = find_team_game(board.games.get_dict(), nba_city)

            if game:
                period = game.get("period", 0)
                clock  = game.get("gameClock", "")
                status = game.get("gameStatusText", "")
                mins   = parse_clock_minutes(clock)
                print(f"  Live: Q{period} | {clock} | {status}")

                if period == 2 and mins <= Q2_TRIGGER_MIN:
                    print(f"  Q2 has ≤{Q2_TRIGGER_MIN} min left — triggering halftime scrape!")
                    return
            else:
                print(f"  {nba_city} game not yet in live scoreboard — waiting...")

        except Exception as e:
            print(f"  Live poll error: {e}")

        time.sleep(POLL_INTERVAL_SEC)


def run_snapshot(event: dict, url: str, snapshot: str, out_csv: str, max_retries: int = 1, team_slug: str = "default", session=None) -> list[dict]:
    if os.path.isfile(out_csv):
        print(f"\n  [{snapshot}] Already exists — skipping scrape. Loading {out_csv}")
        return load_csv(out_csv)
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n[{scraped_at}] Starting {snapshot} scrape...")
    facets, offer_price_map = scrape_listings(url, max_retries=max_retries, team_slug=team_slug, session=session)
    rows = []
    for f in facets:
        rows.extend(parse_facet(f, offer_price_map, scraped_at))
    save_csv(rows, out_csv)
    print_summary(event, rows, out_csv)
    return rows


def main():
    load_dotenv()

    # Prevent Mac from sleeping while this runner is active (display + system).
    # caffeinate exits automatically when this process exits.
    _caffeinate = subprocess.Popen(["caffeinate", "-di"])

    if len(sys.argv) not in (2, 3):
        print("Usage: python run_game.py <team_slug> [YYYY-MM-DD]")
        print("  e.g. python run_game.py warriors 2026-03-11")
        sys.exit(1)

    team      = get_team(sys.argv[1])
    game_date = sys.argv[2] if len(sys.argv) == 3 else None

    max_retries = 4 if team["slug"] in KEEP_ALIVE_TEAMS else 1
    keep_alive  = team["slug"] in KEEP_ALIVE_TEAMS

    print(f"Looking up {team['slug'].title()} home game{' on ' + game_date if game_date else ''} (1 API call)...")
    event   = find_next_home_game(team["tm_keyword"], game_date)
    name    = event.get("name", "Game")
    game_dt = event.get("dates", {}).get("start", {}).get("localDate", "?")
    url     = event.get("url")
    # Some teams (e.g. Wizards) have a non-TM URL in the API — build TM URL from event ID instead
    if not url or "ticketmaster.com" not in url:
        event_id = event.get("id")
        if not event_id:
            raise RuntimeError("Event has no URL or ID in TM API response.")
        url = f"https://www.ticketmaster.com/event/{event_id}"
        print(f"  Non-TM URL detected — using: {url}")

    gdir    = game_dir(team["slug"], game_dt, name)
    pg_csv  = pre_game_csv(gdir)
    ht_csv  = halftime_csv(gdir)
    noshows = no_shows_csv(gdir)
    os.makedirs(gdir, exist_ok=True)

    tipoff        = get_tipoff_utc(event)
    pre_game_time = tipoff - timedelta(minutes=PRE_GAME_OFFSET_MIN)

    print(f"\n  Game:             {name}  ({game_dt})")
    print(f"  Tip-off:          {tipoff.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Pre-game scrape:  {pre_game_time.strftime('%H:%M UTC')}  ({PRE_GAME_OFFSET_MIN} min before tip-off)")
    print(f"  Halftime scrape:  Live clock (Q2 ≤{Q2_TRIGGER_MIN} min)  |  fallback: {HALFTIME_FALLBACK_MIN} min after tip-off")
    if keep_alive:
        print(f"  Keep-alive:       Browser stays open, refreshes TM every {WARM_INTERVAL_MIN} min")
    print(f"  Data folder:      {gdir}/")
    print()

    browser_session = None
    try:
        if keep_alive:
            print("  [keep-alive] Launching persistent browser session...")
            browser_session = launch_browser_session(team["slug"])

        # ── Snapshot 1: pre-game ───────────────────────────────────────────────
        sleep_until(pre_game_time, "pre_game")
        pre_rows = run_snapshot(event, url, "pre_game", pg_csv, max_retries=max_retries, team_slug=team["slug"], session=browser_session)

        # ── Snapshot 2: halftime (live clock) ─────────────────────────────────
        print("\nWaiting for halftime...")
        warm_page = browser_session[2] if browser_session else None  # page object
        wait_for_halftime(tipoff, team["nba_city"], warm_page=warm_page, warm_url=url)
        ht_rows = run_snapshot(event, url, "halftime", ht_csv, max_retries=max_retries, team_slug=team["slug"], session=browser_session)

        # ── Compare ────────────────────────────────────────────────────────────
        print("\nComparing snapshots...")
        pre_rows = load_csv(pg_csv)
        ht_rows  = load_csv(ht_csv)
        no_shows = compare(pre_rows, ht_rows)
        save_no_shows(no_shows, noshows)
        print_report(pre_rows, ht_rows, no_shows, noshows)

    except Exception as e:
        # Clean up folder if no CSV data was saved
        if os.path.isdir(gdir):
            files = [f for f in os.listdir(gdir) if f.endswith(".csv")]
            if not files:
                shutil.rmtree(gdir, ignore_errors=True)
        if "No inventory request captured" in str(e):
            print(f"\n  BOT DETECTION — TM blocked the scrape.")
            _caffeinate.terminate()
            sys.exit(2)
        raise
    finally:
        if browser_session:
            close_browser_session(browser_session[0], browser_session[1])
        _caffeinate.terminate()


if __name__ == "__main__":
    main()
