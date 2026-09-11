"""
nfl_run_game.py

Game-day runner for NFL. Mirrors mlb_run_game.py with NFL-specific timing:
  - Pre-game scrape: 1 hour before kick-off
  - "Halftime": triggered when Q2 has ≤2 min remaining (via ESPN live scoreboard)
    Fallback: 70 minutes after kick-off (typical end of 1st half)

Usage:
    python nfl_run_game.py <team_slug> [YYYY-MM-DD]

    e.g.  python nfl_run_game.py chiefs
          python nfl_run_game.py eagles 2026-09-13
"""

import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from nfl_teams import get_nfl_team, nfl_game_dir
from fetch_listings import (
    find_next_home_game,
    scrape_listings,
    parse_facet,
    parse_seats,
    build_rows_from_embedded_offers,
    save_csv,
    print_summary,
)
from compare_snapshots import load_csv, compare, save_no_shows, print_report
from telegram_notify import send_telegram
import supabase_client

load_dotenv()

PRE_GAME_OFFSET_MIN  = 60    # scrape this many minutes before kick-off
HALFTIME_FALLBACK_MIN = 45   # fallback: minutes after kick-off if live clock unavailable.
                              # Was 70 (aimed at actual halftime), lowered after the
                              # 2026-09-09 Seahawks game: TM listings had already
                              # collapsed from 181 (pre-game) to 4 real seats by the
                              # 71-minute mark, and were fully gone hours later --
                              # whatever is closing that inventory out starts well
                              # before true halftime. 45 min targets roughly halfway
                              # through Q2 (a full NFL quarter runs ~40-45 real
                              # minutes on average with stoppages/TV timeouts), erring
                              # earlier on purpose to catch data before it disappears
                              # rather than precisely hitting the literal Q2 midpoint.
                              # This is a first estimate, not a measured value -- ESPN
                              # (the live-clock source that would trigger earlier,
                              # exactly when real events happen) is blocked from the
                              # runner, confirmed via the smoke-test workflow, so this
                              # fallback is the only timing signal available right now.
                              # Revisit once a real game confirms whether 45 min still
                              # catches meaningful inventory or needs to move earlier.
Q2_TRIGGER_MIN        = 2    # trigger halftime scrape when Q2 ≤ this many minutes
POLL_INTERVAL_SEC     = 30

EASTERN = ZoneInfo("America/New_York")
PRIMETIME_ET_HOUR = 19  # 7 PM ET or later kickoff -- covers SNF/MNF/TNF and
                         # one-off nationally-televised evening games, while
                         # excluding the standard 1 PM / 4:05 / 4:25 PM ET
                         # Sunday afternoon slate.


def get_kickoff_utc(event: dict) -> datetime:
    dt_str = event.get("dates", {}).get("start", {}).get("dateTime")
    if not dt_str:
        raise RuntimeError("Event has no dateTime — cannot schedule automatically.")
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def sleep_until(target: datetime, label: str) -> None:
    now  = datetime.now(timezone.utc)
    wait = (target - now).total_seconds()
    if wait <= 0:
        print(f"  [{label}] Scheduled time already passed — running now.")
        return
    wake = target.strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"  Waiting until {wake} for {label} scrape ({wait / 60:.1f} min)...")
    time.sleep(wait)


def parse_clock_minutes(clock_str: str) -> float:
    """Parse ESPN clock 'PT02M34S' or '2:34' → total minutes as float."""
    m = re.match(r"PT(\d+)M([\d.]+)S", clock_str or "")
    if m:
        return int(m.group(1)) + float(m.group(2)) / 60
    m = re.match(r"(\d+):(\d+)", clock_str or "")
    if m:
        return int(m.group(1)) + int(m.group(2)) / 60
    return 99.0


def wait_for_halftime(kickoff: datetime, espn_tricode: str) -> None:
    """
    Poll ESPN NFL scoreboard every 30s and return when Q2 has ≤2 min left.
    Falls back to HALFTIME_FALLBACK_MIN after kick-off.
    """
    fallback_time = kickoff + timedelta(minutes=HALFTIME_FALLBACK_MIN)
    print(f"  Polling ESPN live NFL clock every {POLL_INTERVAL_SEC}s "
          f"(fallback at {fallback_time.strftime('%H:%M UTC')})...")

    while True:
        if datetime.now(timezone.utc) >= fallback_time:
            print("  Fallback deadline reached — triggering halftime scrape.")
            return

        elapsed = (datetime.now(timezone.utc) - kickoff).total_seconds() / 60
        if elapsed < 15:
            time.sleep(60)
            continue

        try:
            today = datetime.now().strftime("%Y%m%d")
            resp = requests.get(
                f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={today}",
                headers={"User-Agent": "Mozilla/5.0"}, timeout=10,
            )
            resp.raise_for_status()
            for event in resp.json().get("events", []):
                comps = event["competitions"][0]
                home = next((t for t in comps["competitors"] if t["homeAway"] == "home"), None)
                if not home or home["team"]["abbreviation"] != espn_tricode:
                    continue
                status = event.get("status", {})
                period = status.get("period", 0)
                clock  = status.get("displayClock", "")
                mins   = parse_clock_minutes(clock)
                print(f"  Live: Q{period} | {clock}")
                if period == 2 and mins <= Q2_TRIGGER_MIN:
                    print(f"  Q2 ≤{Q2_TRIGGER_MIN} min — triggering halftime scrape!")
                    return
        except Exception as e:
            print(f"  Live poll error: {e}")

        time.sleep(POLL_INTERVAL_SEC)


def run_snapshot(event: dict, url: str, snapshot: str, out_csv: str, team_slug: str) -> list[dict]:
    if os.path.isfile(out_csv):
        print(f"\n  [{snapshot}] Already exists — loading {out_csv}")
        return load_csv(out_csv)
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n[{scraped_at}] Starting {snapshot} scrape...")
    facets, offer_price_map, places_facets, embedded_offers = scrape_listings(url, max_retries=1, team_slug=team_slug)
    if embedded_offers:
        rows = build_rows_from_embedded_offers(embedded_offers, scraped_at)
    elif places_facets:
        rows = parse_seats(facets, places_facets, offer_price_map, scraped_at)
    else:
        rows = []
        for f in facets:
            rows.extend(parse_facet(f, offer_price_map, scraped_at))
    save_csv(rows, out_csv)
    print_summary(event, rows, out_csv)
    return rows


def save_game_meta(event: dict, team: dict, gdir: str) -> dict:
    path = os.path.join(gdir, "game_meta.json")
    if os.path.isfile(path):
        return json.load(open(path))

    name    = event.get("name", "")
    game_dt = event.get("dates", {}).get("start", {}).get("localDate", "")
    local_t = event.get("dates", {}).get("start", {}).get("localTime", "")
    arena   = event.get("_embedded", {}).get("venues", [{}])[0].get("name", "")
    city    = event.get("_embedded", {}).get("venues", [{}])[0].get("city", {}).get("name", "")

    opponent = ""
    for sep in (" vs. ", " v. ", " vs ", " v ", " at "):
        if sep in name:
            opponent = name.split(sep, 1)[1].strip()
            break

    day_of_week = ""
    if game_dt:
        try:
            day_of_week = datetime.strptime(game_dt, "%Y-%m-%d").strftime("%A")
        except ValueError:
            pass

    meta = {
        "home_team":    team["slug"],
        "opponent":     opponent,
        "game_date":    game_dt,
        "day_of_week":  day_of_week,
        "tipoff_local": local_t[:5] if local_t else "",
        "arena":        arena,
        "city":         city,
        "league":       "nfl",
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"  Game meta saved → {path}")
    return meta


def is_primetime(kickoff_utc: datetime) -> bool:
    return kickoff_utc.astimezone(EASTERN).hour >= PRIMETIME_ET_HOUR


def notify_scrape_status(team_slug: str, primetime: bool, message: str) -> None:
    """Telegram status ping for 49ers games and primetime games (evening
    kickoffs), so there's visibility into scrape health for every game
    worth watching without checking every single one manually."""
    if team_slug == "49ers" or primetime:
        send_telegram(message)


def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: python nfl_run_game.py <team_slug> [YYYY-MM-DD]")
        print("  e.g. python nfl_run_game.py chiefs 2026-09-13")
        sys.exit(1)

    team      = get_nfl_team(sys.argv[1])
    game_date = sys.argv[2] if len(sys.argv) == 3 else None

    print(f"Looking up {team['slug'].title()} home game{' on ' + game_date if game_date else ''}...")
    event   = find_next_home_game(team["tm_keyword"], game_date, classification="Football")
    name    = event.get("name", "Game")
    game_dt = event.get("dates", {}).get("start", {}).get("localDate", "?")
    url     = event.get("url")
    if not url or "ticketmaster.com" not in url:
        event_id = event.get("id")
        if not event_id:
            raise RuntimeError("Event has no URL or ID in TM API response.")
        url = f"https://www.ticketmaster.com/event/{event_id}"

    opponent = name
    for sep in (" vs. ", " v. ", " vs ", " at "):
        if sep in name:
            opponent = name.split(sep, 1)[1].strip()
            break

    gdir    = nfl_game_dir(team["slug"], game_dt, opponent)
    pg_csv  = os.path.join(gdir, "pre_game.csv")
    ht_csv  = os.path.join(gdir, "halftime.csv")
    ns_csv  = os.path.join(gdir, "no_shows.csv")
    os.makedirs(gdir, exist_ok=True)

    meta    = save_game_meta(event, team, gdir)
    game_id = supabase_client.upsert_game(meta, league="nfl")

    kickoff       = get_kickoff_utc(event)
    pre_game_time = kickoff - timedelta(minutes=PRE_GAME_OFFSET_MIN)
    primetime     = is_primetime(kickoff)

    print(f"\n  Game:            {name}  ({game_dt})")
    print(f"  Kick-off:        {kickoff.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Pre-game scrape: {pre_game_time.strftime('%H:%M UTC')}  ({PRE_GAME_OFFSET_MIN} min before kick-off)")
    print(f"  Halftime scrape: Live clock (Q2 ≤{Q2_TRIGGER_MIN} min)  |  fallback: {HALFTIME_FALLBACK_MIN} min after kick-off")
    print(f"  Data folder:     {gdir}/")
    print(f"  Telegram alerts: {'yes (49ers or primetime)' if (team['slug'] == '49ers' or primetime) else 'no'}\n")

    sleep_until(pre_game_time, "pre_game")
    # Widened from 240s -> 480s (8 min). On a Sunday with 8+ teams sharing a
    # 1:00 PM ET kickoff, every one of those subprocesses hits this line at
    # the same instant and each opens a real, full (non-headless) Chrome
    # window via launch_browser_session() on the SAME single runner machine
    # -- a small jitter window meant several still landed in the same
    # 30-second slice. There's no timing pressure here (60 min of buffer
    # before kick-off), so spread further to keep simultaneous browser
    # launches to a handful instead of most of them at once.
    jitter = random.randint(0, 480)
    if jitter:
        print(f"  [jitter] Waiting {jitter}s before scrape...")
        time.sleep(jitter)

    team_label = team["slug"].title()

    try:
        pre_rows = run_snapshot(event, url, "pre_game", pg_csv, team["slug"])
        supabase_client.insert_listings(game_id, pre_rows, "pre_game", team["slug"], game_dt, league="nfl")
        priced = sum(1 for r in pre_rows if r.get("price_usd") is not None)
        notify_scrape_status(team["slug"], primetime,
            f"{team_label} pregame scrape done: {len(pre_rows)} seats found, {priced} priced. "
            f"vs {opponent} ({game_dt}).")
    except Exception as e:
        notify_scrape_status(team["slug"], primetime, f"{team_label} pregame scrape FAILED: {e}")
        raise

    print("\nWaiting for halftime...")
    wait_for_halftime(kickoff, team["espn_tricode"])

    # Same clustering problem as the pre-game scrape, but here we can't
    # spread as wide -- TM's own listings are actively collapsing as the
    # game proceeds (see HALFTIME_FALLBACK_MIN comment above), so a long
    # delay costs real data. A short jitter is enough to avoid every
    # same-kickoff-time team launching a browser in the exact same second.
    ht_jitter = random.randint(0, 90)
    if ht_jitter:
        print(f"  [jitter] Waiting {ht_jitter}s before halftime scrape...")
        time.sleep(ht_jitter)

    try:
        ht_rows = run_snapshot(event, url, "halftime", ht_csv, team["slug"])
        supabase_client.insert_listings(game_id, ht_rows, "halftime", team["slug"], game_dt, league="nfl")
        priced = sum(1 for r in ht_rows if r.get("price_usd") is not None)
        notify_scrape_status(team["slug"], primetime,
            f"{team_label} halftime scrape done: {len(ht_rows)} seats still listed, {priced} priced. "
            f"vs {opponent} ({game_dt}).")
    except Exception as e:
        notify_scrape_status(team["slug"], primetime, f"{team_label} halftime scrape FAILED: {e}")
        raise

    print("\nComparing snapshots...")
    pre_rows = load_csv(pg_csv)
    # save_csv() legitimately writes nothing when a scrape returns zero
    # rows (confirmed live, 2026-09-09: the halftime scrape got back only
    # 4 real seats from TM, well below the threshold below, and never even
    # got that far because this used to crash on the missing file first) --
    # a missing halftime.csv is now a valid, expected state, not an error.
    ht_rows = load_csv(ht_csv) if os.path.isfile(ht_csv) else []

    # Sanity gate: compare() treats every seat present in both snapshots as
    # a no-show. If the halftime listing count has collapsed far below
    # pre-game (confirmed live: 181 pre-game -> 4 at halftime, ~98% "no-show"),
    # that is not real fan behavior -- it means TM's own resale marketplace
    # emptied out the listings, not that fans failed to show up. Inserting
    # that as if it were real no-show data would be actively misleading
    # (a fabricated ~98% no-show rate is worse than no data at all). 15% is
    # a first estimate for "this isn't real attendance signal anymore", not
    # a measured value -- revisit once more real games establish what a
    # normal halftime listing count actually looks like relative to pre-game.
    MIN_HALFTIME_RATIO = 0.15
    if pre_rows and len(ht_rows) < len(pre_rows) * MIN_HALFTIME_RATIO:
        msg = (f"Halftime listings collapsed to {len(ht_rows)} from {len(pre_rows)} pre-game "
               f"(below {MIN_HALFTIME_RATIO:.0%}) -- likely TM's marketplace closing out "
               f"listings, not real no-shows. Skipping no-show insert to avoid recording "
               f"fabricated data.")
        print(f"  {msg}")
        notify_scrape_status(team["slug"], primetime, f"{team_label}: {msg}")
        return

    no_shows = compare(pre_rows, ht_rows)
    save_no_shows(no_shows, ns_csv)
    supabase_client.insert_no_shows(game_id, no_shows, team["slug"], game_dt, league="nfl")
    print_report(pre_rows, ht_rows, no_shows, ns_csv)


if __name__ == "__main__":
    main()
