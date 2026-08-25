"""
wnba_run_game.py

Runs the full data-collection lifecycle for one WNBA home game:
  1. Look up the game on Ticketmaster.
  2. Sleep until 60 min before tip-off → pre-game snapshot.
  3. Poll ESPN live scoreboard → scrape halftime snapshot when Q2 ends.
  4. Compare snapshots, save no-shows, push to Supabase.

Usage:
    python wnba_run_game.py <team_slug> [YYYY-MM-DD]
    e.g.  python wnba_run_game.py liberty 2026-06-30
"""

import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv
load_dotenv()

from wnba_teams import get_wnba_team, wnba_game_dir
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
import requests as req

PRE_GAME_OFFSET_MIN   = 60
HALFTIME_FALLBACK_MIN = 55   # fallback if live data unavailable
POLL_INTERVAL_SEC     = 30
Q2_TRIGGER_MIN        = 2    # scrape when Q2 clock ≤ this many minutes


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_tipoff_utc(event: dict) -> datetime:
    dt_str = event.get("dates", {}).get("start", {}).get("dateTime")
    if not dt_str:
        raise RuntimeError("Event has no dateTime field.")
    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))


def sleep_until(target: datetime, label: str) -> None:
    now  = datetime.now(timezone.utc)
    wait = (target - now).total_seconds()
    if wait <= 0:
        print(f"  [{label}] Scheduled time already passed — running now.")
        return
    wake = target.strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"  Waiting until {wake} for {label} ({wait / 60:.1f} min)...", flush=True)
    chunk = 300
    while wait > 0:
        time.sleep(min(chunk, wait))
        wait -= chunk
        remaining = (target - datetime.now(timezone.utc)).total_seconds()
        if remaining > chunk:
            print(f"  [{label}] ~{remaining/60:.0f} min remaining...", flush=True)


def parse_clock_minutes(clock_str: str) -> float:
    """Parse ESPN clock string like '2:34' → total minutes as float."""
    if not clock_str:
        return 99.0
    parts = clock_str.split(":")
    if len(parts) == 2:
        return int(parts[0]) + int(parts[1]) / 60
    return 99.0


def get_live_game_status(espn_abbr: str, today: str):
    """
    Poll ESPN WNBA scoreboard for the given team's live game status.
    Returns (period, clock_minutes, status_description) or None if not found.
    """
    date_str = today.replace("-", "")
    url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/wnba/scoreboard?dates={date_str}"
    try:
        resp = req.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [live] ESPN fetch error: {e}")
        return None

    for event in resp.json().get("events", []):
        comp = event["competitions"][0]
        for team in comp["competitors"]:
            if team["team"]["abbreviation"] == espn_abbr:
                status = comp["status"]
                period = status.get("period", 0)
                clock  = status.get("displayClock", "")
                desc   = status["type"]["description"]
                return period, parse_clock_minutes(clock), desc
    return None


def wait_for_halftime(tipoff: datetime, espn_abbr: str, today: str) -> None:
    """Poll until Q2 has ≤ Q2_TRIGGER_MIN minutes left, then return."""
    fallback = tipoff + timedelta(minutes=HALFTIME_FALLBACK_MIN)
    now      = datetime.now(timezone.utc)

    if now < tipoff:
        print(f"  Waiting for tip-off ({tipoff.strftime('%H:%M UTC')}) before polling live clock...")
        time.sleep(max(0, (tipoff - now).total_seconds()))

    print(f"  Polling ESPN live clock every {POLL_INTERVAL_SEC}s (fallback at {fallback.strftime('%H:%M UTC')})...")
    while True:
        if datetime.now(timezone.utc) >= fallback:
            print("  Fallback time reached — triggering halftime scrape.")
            return

        status = get_live_game_status(espn_abbr, today)
        if status:
            period, mins, desc = status
            print(f"  Live: Q{period} | {mins:.1f} min | {desc}")
            if period == 2 and mins <= Q2_TRIGGER_MIN:
                print(f"  Q2 has ≤{Q2_TRIGGER_MIN} min — triggering halftime scrape!")
                return
            if desc in ("Halftime", "Final"):
                print(f"  Game status: {desc} — triggering halftime scrape.")
                return
        else:
            print("  Live data unavailable — retrying...")

        time.sleep(POLL_INTERVAL_SEC)


def save_game_meta(event: dict, team: dict, gdir: str) -> dict:
    name    = event.get("name", "Game")
    game_dt = event.get("dates", {}).get("start", {}).get("localDate", "")
    venue   = event.get("_embedded", {}).get("venues", [{}])[0]

    opponent = name
    for sep in (" vs. ", " v. ", " vs ", " v "):
        if sep in name:
            opponent = name.split(sep, 1)[1].strip()
            break

    tipoff_local = event.get("dates", {}).get("start", {}).get("localTime", "")[:5]
    day_of_week  = datetime.strptime(game_dt, "%Y-%m-%d").strftime("%A") if game_dt else ""

    meta = {
        "home_team":    team["slug"],
        "opponent":     opponent,
        "game_date":    game_dt,
        "day_of_week":  day_of_week,
        "tipoff_local": tipoff_local,
        "arena":        venue.get("name", ""),
        "city":         venue.get("city", {}).get("name", ""),
        "league":       "wnba",
    }

    meta_path = os.path.join(gdir, "game_meta.json")
    if not os.path.isfile(meta_path):
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
        print(f"  Game meta saved → {meta_path}")

    return meta


def run_snapshot(event: dict, url: str, snapshot: str, out_csv: str) -> list[dict]:
    if os.path.isfile(out_csv):
        print(f"\n  [{snapshot}] Already exists — loading {out_csv}")
        return load_csv(out_csv)

    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n[{scraped_at}] Starting {snapshot} scrape...")
    facets, offer_price_map, places_facets = scrape_listings(url, max_retries=3)

    if places_facets:
        rows = parse_seats(facets, places_facets, offer_price_map, scraped_at)
    else:
        rows = []
        for f in facets:
            rows.extend(parse_facet(f, offer_price_map, scraped_at))

    save_csv(rows, out_csv)
    print_summary(event, rows, out_csv)
    return rows


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: python wnba_run_game.py <team_slug> [YYYY-MM-DD]")
        sys.exit(1)

    team      = get_wnba_team(sys.argv[1])
    game_date = sys.argv[2] if len(sys.argv) == 3 else None
    today     = game_date or datetime.now().strftime("%Y-%m-%d")

    print(f"Looking up {team['slug'].title()} home game{' on ' + game_date if game_date else ''}...")
    try:
        event = find_next_home_game(team["tm_keyword"], game_date, classification="Basketball")
    except RuntimeError as e:
        print(f"  No TM listing: {e} — skipping.")
        sys.exit(0)

    name    = event.get("name", "Game")
    game_dt = event.get("dates", {}).get("start", {}).get("localDate", today)
    url     = event.get("url")
    if not url or "ticketmaster.com" not in url:
        event_id = event.get("id")
        url = f"https://www.ticketmaster.com/event/{event_id}"

    opponent = name
    for sep in (" vs. ", " v. ", " vs ", " v "):
        if sep in name:
            opponent = name.split(sep, 1)[1].strip()
            break

    gdir    = wnba_game_dir(team["slug"], game_dt, opponent)
    pg_csv  = os.path.join(gdir, "pre_game.csv")
    ht_csv  = os.path.join(gdir, "halftime.csv")
    ns_csv  = os.path.join(gdir, "no_shows.csv")
    os.makedirs(gdir, exist_ok=True)

    meta    = save_game_meta(event, team, gdir)
    game_id = supabase_client.upsert_game(meta, league="wnba")

    tipoff        = get_tipoff_utc(event)
    pre_game_time = tipoff - timedelta(minutes=PRE_GAME_OFFSET_MIN)
    now_utc       = datetime.now(timezone.utc)

    print(f"\n  Game:             {name}  ({game_dt})")
    print(f"  Tip-off:          {tipoff.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Pre-game scrape:  {pre_game_time.strftime('%H:%M UTC')}  ({PRE_GAME_OFFSET_MIN} min before tip)")
    print(f"  Data folder:      {gdir}/\n")

    if now_utc >= tipoff and not os.path.isfile(pg_csv):
        print("  Game already started — pre-game window missed. Skipping.")
        sys.exit(0)

    sleep_until(pre_game_time, "pre_game")
    jitter = random.randint(30, 600)
    print(f"  [jitter] Waiting {jitter}s before scrape...")
    time.sleep(jitter)

    pre_rows = run_snapshot(event, url, "pre_game", pg_csv)
    supabase_client.insert_listings(game_id, pre_rows, "pre_game", team["slug"], game_dt, league="wnba")

    print("\nWaiting for halftime...")
    wait_for_halftime(tipoff, team["espn_abbr"], today)

    ht_rows = run_snapshot(event, url, "halftime", ht_csv)
    supabase_client.insert_listings(game_id, ht_rows, "halftime", team["slug"], game_dt, league="wnba")

    if pre_rows and ht_rows:
        no_shows = compare(pre_rows, ht_rows)
        save_no_shows(no_shows, ns_csv)
        print_report(pre_rows, ht_rows, no_shows, meta)
        supabase_client.insert_no_shows(game_id, no_shows, team["slug"], game_dt, league="wnba")


if __name__ == "__main__":
    main()
