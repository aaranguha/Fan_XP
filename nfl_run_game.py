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

import requests
from dotenv import load_dotenv

from nfl_teams import get_nfl_team, nfl_game_dir
from fetch_listings import (
    find_next_home_game,
    scrape_listings,
    parse_facet,
    parse_seats,
    save_csv,
    print_summary,
)
from compare_snapshots import load_csv, compare, save_no_shows, print_report
import supabase_client

load_dotenv()

PRE_GAME_OFFSET_MIN  = 60    # scrape this many minutes before kick-off
HALFTIME_FALLBACK_MIN = 70   # fallback: minutes after kick-off if live clock unavailable
Q2_TRIGGER_MIN        = 2    # trigger halftime scrape when Q2 ≤ this many minutes
POLL_INTERVAL_SEC     = 30


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
    facets, offer_price_map, places_facets = scrape_listings(url, max_retries=1, team_slug=team_slug)
    if places_facets:
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

    print(f"\n  Game:            {name}  ({game_dt})")
    print(f"  Kick-off:        {kickoff.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Pre-game scrape: {pre_game_time.strftime('%H:%M UTC')}  ({PRE_GAME_OFFSET_MIN} min before kick-off)")
    print(f"  Halftime scrape: Live clock (Q2 ≤{Q2_TRIGGER_MIN} min)  |  fallback: {HALFTIME_FALLBACK_MIN} min after kick-off")
    print(f"  Data folder:     {gdir}/\n")

    sleep_until(pre_game_time, "pre_game")
    jitter = random.randint(0, 240)
    if jitter:
        print(f"  [jitter] Waiting {jitter}s before scrape...")
        time.sleep(jitter)

    pre_rows = run_snapshot(event, url, "pre_game", pg_csv, team["slug"])
    supabase_client.insert_listings(game_id, pre_rows, "pre_game", team["slug"], game_dt, league="nfl")

    print("\nWaiting for halftime...")
    wait_for_halftime(kickoff, team["espn_tricode"])

    ht_rows = run_snapshot(event, url, "halftime", ht_csv, team["slug"])
    supabase_client.insert_listings(game_id, ht_rows, "halftime", team["slug"], game_dt, league="nfl")

    print("\nComparing snapshots...")
    pre_rows = load_csv(pg_csv)
    ht_rows  = load_csv(ht_csv)
    no_shows = compare(pre_rows, ht_rows)
    save_no_shows(no_shows, ns_csv)
    supabase_client.insert_no_shows(game_id, no_shows, team["slug"], game_dt, league="nfl")
    print_report(pre_rows, ht_rows, no_shows, ns_csv)


if __name__ == "__main__":
    main()
