"""
mlb_run_game.py

Game-day runner for MLB. Mirrors run_game.py for NBA with two differences:
  1. Schedule / event lookup uses 'Baseball' classification on Ticketmaster.
  2. "Halftime" = mid-game scrape, triggered when the 3rd inning starts
     (polled via MLB Stats API). Falls back to 75 min after first pitch.
     TM cuts off resale listings ~3rd inning, so earlier = more actionable.

Usage:
    python mlb_run_game.py <team_slug> [YYYY-MM-DD]

    e.g.  python mlb_run_game.py yankees
          python mlb_run_game.py dodgers 2026-07-04
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

from mlb_teams import get_mlb_team, mlb_game_dir
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

# ── Timing ────────────────────────────────────────────────────────────────────
PRE_GAME_OFFSET_MIN  = 60     # scrape this many minutes before first pitch
MID_GAME_INNING      = 3     # scrape when this inning starts (TM cuts off ~3rd)
MID_GAME_FALLBACK_H  = 1.25  # fallback: 75 min after first pitch
POLL_INTERVAL_SEC    = 60   # how often to poll MLB live feed


def get_first_pitch_utc(event: dict) -> datetime:
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


def get_mlb_game_pk(mlb_team_id: int, today: str) -> int | None:
    """Look up the MLB Stats API gamePk for today's game."""
    try:
        resp = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId": 1, "date": today, "teamId": mlb_team_id},
            timeout=10,
        )
        resp.raise_for_status()
        dates = resp.json().get("dates", [])
        if dates and dates[0].get("games"):
            return dates[0]["games"][0]["gamePk"]
    except Exception as e:
        print(f"  [mlb api] Could not get gamePk: {e}")
    return None


def wait_for_mid_game(first_pitch: datetime, mlb_team_id: int, today: str) -> None:
    """
    Poll MLB Stats API every 60s and return when the MID_GAME_INNING starts.
    Falls back to MID_GAME_FALLBACK_H hours after first pitch if live data unavailable.
    """
    fallback_time = first_pitch + timedelta(hours=MID_GAME_FALLBACK_H)
    game_pk = get_mlb_game_pk(mlb_team_id, today)

    if not game_pk:
        print(f"  No gamePk found — using {MID_GAME_FALLBACK_H}h fallback.")
        sleep_until(fallback_time, "mid_game")
        return

    print(f"  Polling MLB live feed every {POLL_INTERVAL_SEC}s (gamePk={game_pk}, fallback at {fallback_time.strftime('%H:%M UTC')})...")

    while True:
        if datetime.now(timezone.utc) >= fallback_time:
            print("  Fallback deadline reached — triggering mid-game scrape.")
            return

        elapsed = (datetime.now(timezone.utc) - first_pitch).total_seconds() / 60
        if elapsed < 30:
            time.sleep(60)
            continue

        try:
            resp = requests.get(
                f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live",
                timeout=10,
            )
            resp.raise_for_status()
            linescore = resp.json().get("liveData", {}).get("linescore", {})
            inning    = linescore.get("currentInning", 0)
            half      = linescore.get("inningHalf", "")
            print(f"  Live: Inning {inning} ({half})")

            if inning >= MID_GAME_INNING:
                print(f"  Inning {inning} reached — triggering mid-game scrape!")
                return
        except Exception as e:
            print(f"  Live poll error: {e}")

        time.sleep(POLL_INTERVAL_SEC)


def run_snapshot(event: dict, url: str, snapshot: str, out_csv: str) -> list[dict]:
    if os.path.isfile(out_csv):
        print(f"\n  [{snapshot}] Already exists — loading {out_csv}")
        return load_csv(out_csv)
    scraped_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"\n[{scraped_at}] Starting {snapshot} scrape...")
    facets, offer_price_map, places_facets = scrape_listings(url, max_retries=1)
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
    for sep in (" vs. ", " v. ", " vs ", " v "):
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
        "home_team":   team["slug"],
        "opponent":    opponent,
        "game_date":   game_dt,
        "day_of_week": day_of_week,
        "tipoff_local": local_t[:5] if local_t else "",
        "arena":        arena,
        "city":         city,
        "league":       "mlb",
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    print(f"  Game meta saved → {path}")
    return meta


def main():
    if len(sys.argv) not in (2, 3):
        print("Usage: python mlb_run_game.py <team_slug> [YYYY-MM-DD]")
        print("  e.g. python mlb_run_game.py yankees 2026-07-04")
        sys.exit(1)

    team      = get_mlb_team(sys.argv[1])
    game_date = sys.argv[2] if len(sys.argv) == 3 else None
    today     = game_date or datetime.now().strftime("%Y-%m-%d")

    print(f"Looking up {team['slug'].title()} home game{' on ' + game_date if game_date else ''}...")
    event   = find_next_home_game(team["tm_keyword"], game_date, classification="Baseball")
    name    = event.get("name", "Game")
    game_dt = event.get("dates", {}).get("start", {}).get("localDate", "?")
    url     = event.get("url")
    if not url or "ticketmaster.com" not in url:
        event_id = event.get("id")
        if not event_id:
            raise RuntimeError("Event has no URL or ID in TM API response.")
        url = f"https://www.ticketmaster.com/event/{event_id}"

    # Parse opponent for folder name
    opponent = name
    for sep in (" vs. ", " v. ", " vs ", " v "):
        if sep in name:
            opponent = name.split(sep, 1)[1].strip()
            break

    gdir    = mlb_game_dir(team["slug"], game_dt, opponent)
    pg_csv  = os.path.join(gdir, "pre_game.csv")
    mid_csv = os.path.join(gdir, "mid_game.csv")
    ns_csv  = os.path.join(gdir, "no_shows.csv")
    os.makedirs(gdir, exist_ok=True)

    meta     = save_game_meta(event, team, gdir)
    game_id  = supabase_client.upsert_game(meta, league="mlb")

    first_pitch     = get_first_pitch_utc(event)
    pre_game_time   = first_pitch - timedelta(minutes=PRE_GAME_OFFSET_MIN)
    now_utc         = datetime.now(timezone.utc)

    print(f"\n  Game:              {name}  ({game_dt})")
    print(f"  First pitch:       {first_pitch.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Pre-game scrape:   {pre_game_time.strftime('%H:%M UTC')}  ({PRE_GAME_OFFSET_MIN} min before pitch)")
    print(f"  Mid-game scrape:   Live clock (inning ≥ {MID_GAME_INNING})  |  fallback: {MID_GAME_FALLBACK_H}h after pitch)")
    print(f"  Data folder:       {gdir}/\n")

    # If the game has already started (past first pitch) and no pre-game data, skip gracefully
    if now_utc >= first_pitch and not os.path.isfile(pg_csv):
        print(f"  Game already started — pre-game window missed. Skipping.")
        sys.exit(0)

    # Jitter 0–4 min so simultaneous runners don't all hit TM at once
    sleep_until(pre_game_time, "pre_game")
    jitter = random.randint(0, 240)
    if jitter:
        print(f"  [jitter] Waiting {jitter}s before scrape...")
        time.sleep(jitter)

    pre_rows = run_snapshot(event, url, "pre_game", pg_csv)
    supabase_client.insert_listings(game_id, pre_rows, "pre_game", team["slug"], game_dt, league="mlb")

    print("\nWaiting for mid-game...")
    wait_for_mid_game(first_pitch, team["mlb_team_id"], today)

    mid_rows = run_snapshot(event, url, "mid_game", mid_csv)
    supabase_client.insert_listings(game_id, mid_rows, "mid_game", team["slug"], game_dt, league="mlb")

    print("\nComparing snapshots...")
    pre_rows = load_csv(pg_csv)
    mid_rows = load_csv(mid_csv)
    no_shows = compare(pre_rows, mid_rows)
    save_no_shows(no_shows, ns_csv)
    supabase_client.insert_no_shows(game_id, no_shows, team["slug"], game_dt, league="mlb")
    print_report(pre_rows, mid_rows, no_shows, ns_csv)


if __name__ == "__main__":
    main()
