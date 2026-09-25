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
from pathlib import Path

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
# A "pre-game" baseline taken closer to kick-off than this is not a baseline:
# fans are already seated and TM's resale page is winding down, so diffing it
# against a halftime scrape minutes later fabricates a near-100% no-show rate
# (happened 2026-09-14 Chiefs and 2026-09-17 Bills, when the workflow fired
# ~2h late and both scrapes ran 2 minutes apart after kick-off).
MIN_PREGAME_LEAD_MIN = 20
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


def save_game_meta(event: dict, team: dict, gdir: str, opponent: str) -> dict:
    path = os.path.join(gdir, "game_meta.json")
    if os.path.isfile(path):
        return json.load(open(path))

    game_dt = event.get("dates", {}).get("start", {}).get("localDate", "")
    local_t = event.get("dates", {}).get("start", {}).get("localTime", "")
    arena   = event.get("_embedded", {}).get("venues", [{}])[0].get("name", "")
    city    = event.get("_embedded", {}).get("venues", [{}])[0].get("city", {}).get("name", "")

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


def notify_scrape_status(message: str) -> None:
    """Telegram ping with a game's final outcome: scraped or not, and why.
    Exactly one per game, for every game (the founder asked for only this,
    not step-by-step progress). Intermediate successes are not reported."""
    send_telegram(message)


def parse_opponent_name(event_name: str) -> str:
    """Extract the opponent's name from a TM event name like "Kansas City
    Chiefs vs. Denver Broncos".

    TM's own event names are inconsistent about this separator: "vs.",
    "vs", "v.", or a bare "v"/"V" (confirmed live: "Kansas City Chiefs v
    Denver Broncos" and "Los Angeles Chargers V Arizona Cardinals" both
    slipped through the old literal-substring check, which required a
    period or trailing "s" and was case-sensitive, leaving `opponent` as
    the entire unparsed event name). Match case-insensitively as a
    standalone word instead of a fixed literal list. If no separator is
    found, the whole event name is returned unchanged.

    Promo suffixes after " - " or ": " are dropped (confirmed live:
    "San Francisco 49ers vs. Arizona Cardinals - George Kittle Bobblehead"),
    since no NFL team name contains either.
    """
    sep_match = re.search(r"\s+(?:vs\.?|v\.?|at)\s+", event_name, re.IGNORECASE)
    if sep_match:
        opponent = event_name[sep_match.end():]
        return re.split(r"\s+-\s+|:\s+", opponent, maxsplit=1)[0].strip()
    return event_name


MIN_HALFTIME_RATIO = 0.15


class SnapshotEvaluation:
    """Result of evaluate_snapshot_quality(): `verdict` is one of
    "no_pregame", "marketplace_collapsed", "impossible_no_shows", or "ok".
    `no_shows` is only populated once compare() has actually run (i.e. not
    for "no_pregame"/"marketplace_collapsed", where comparing would be
    meaningless)."""

    def __init__(self, verdict: str, no_shows: list):
        self.verdict = verdict
        self.no_shows = no_shows


def evaluate_snapshot_quality(pre_rows: list[dict], ht_rows: list[dict]) -> SnapshotEvaluation:
    """Decide whether a pre-game/halftime snapshot pair is trustworthy enough
    to record as no-shows, and run compare() only once it's clear doing so
    is meaningful.

    Three gates, checked in order (each is a real incident, see CLAUDE.md
    §4.2/§4.3):
    1. No pre-game listings at all -> no baseline to compare against.
    2. Halftime listings collapsed to below MIN_HALFTIME_RATIO of pre-game
       (confirmed live: 181 pre-game -> 4 at halftime, ~98% "no-show") --
       this reflects TM's own resale marketplace emptying out as an event
       proceeds, not real fan no-shows. Recording it would fabricate a
       near-100% no-show rate.
    3. no_shows count exceeding the smaller snapshot -- mathematically
       impossible for a real intersection (a no-show is a seat present in
       BOTH snapshots), almost certainly two mismatched/duplicate snapshots
       getting compared (confirmed live 2026-09-20: no_shows=1908 against a
       pre-game count of only 173, from a git-push-failure incident).
    """
    if not pre_rows:
        return SnapshotEvaluation("no_pregame", [])

    if len(ht_rows) < len(pre_rows) * MIN_HALFTIME_RATIO:
        return SnapshotEvaluation("marketplace_collapsed", [])

    no_shows = compare(pre_rows, ht_rows)

    if no_shows and len(no_shows) > min(len(pre_rows), len(ht_rows)):
        return SnapshotEvaluation("impossible_no_shows", no_shows)

    return SnapshotEvaluation("ok", no_shows)


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

    opponent = parse_opponent_name(name)

    gdir    = nfl_game_dir(team["slug"], game_dt, opponent)
    pg_csv  = os.path.join(gdir, "pre_game.csv")
    ht_csv  = os.path.join(gdir, "halftime.csv")
    ns_csv  = os.path.join(gdir, "no_shows.csv")
    done_marker = os.path.join(gdir, ".scrape_complete")
    os.makedirs(gdir, exist_ok=True)

    # Guards against a second same-day run (a manual re-run, or a restart
    # after a code fix) re-scraping a game that's already done. Originally
    # added when nfl.yml fired twice on Sundays - confirmed live 2026-09-13: the Giants' pregame
    # scrape ran fine at the noon trigger, then the 6pm trigger launched a
    # SECOND giants subprocess that tried to "pre-game" scrape a game that
    # was already at/past halftime, timing out against the live event page.
    # A completed run leaves this marker so a same-day re-discovery is a
    # cheap no-op instead of a duplicate (and likely failing) scrape. Only
    # written on a clean finish, not on an exception, so a genuinely failed
    # attempt (e.g. a real transient timeout) stays eligible for the later
    # trigger to retry.
    if os.path.isfile(done_marker):
        print(f"  {team['slug'].title()} vs {opponent} ({game_dt}) was already fully "
              f"scraped earlier today - skipping duplicate run.")
        return

    meta    = save_game_meta(event, team, gdir, opponent)
    game_id = supabase_client.upsert_game(meta, league="nfl")

    kickoff       = get_kickoff_utc(event)
    pre_game_time = kickoff - timedelta(minutes=PRE_GAME_OFFSET_MIN)
    team_label    = team["slug"].title()
    game_label    = f"{team_label} vs {opponent} ({game_dt})"

    print(f"\n  Game:            {name}  ({game_dt})")
    print(f"  Kick-off:        {kickoff.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Pre-game scrape: {pre_game_time.strftime('%H:%M UTC')}  ({PRE_GAME_OFFSET_MIN} min before kick-off)")
    print(f"  Halftime scrape: Live clock (Q2 ≤{Q2_TRIGGER_MIN} min)  |  fallback: {HALFTIME_FALLBACK_MIN} min after kick-off")
    print(f"  Data folder:     {gdir}/")
    print(f"  Telegram alert:  one outcome message when the game finishes\n")

    sleep_until(pre_game_time, "pre_game")
    if os.path.isfile(done_marker):
        # A stray duplicate instance (e.g. an orphaned process from a
        # cancelled-and-restarted job) can sleep here for hours; re-check
        # right after waking, since another instance may have finished the
        # whole game in the meantime.
        print(f"  {team_label} vs {opponent} ({game_dt}) was completed by another "
              f"run while this one was waiting - skipping.")
        return
    # Widened from 240s -> 480s (8 min). On a Sunday with 8+ teams sharing a
    # 1:00 PM ET kickoff, every one of those subprocesses hits this line at
    # the same instant and each opens a real, full (non-headless) Chrome
    # window via launch_browser_session() on the SAME single runner machine
    # -- a small jitter window meant several still landed in the same
    # 30-second slice. There's no timing pressure here (60 min of buffer
    # before kick-off), so spread further to keep simultaneous browser
    # launches to a handful instead of most of them at once.
    # Never let the jitter itself push the scrape inside MIN_PREGAME_LEAD_MIN.
    lead_s = (kickoff - datetime.now(timezone.utc)).total_seconds() - MIN_PREGAME_LEAD_MIN * 60
    if lead_s <= 0:
        mins_late = (datetime.now(timezone.utc) - pre_game_time).total_seconds() / 60
        msg = (f"{game_label}: NOT scraped. The runner started {mins_late:.0f} min after the "
               f"pre-game window (kick-off {kickoff.strftime('%H:%M UTC')}), too late for a "
               f"real baseline. Nothing recorded.")
        print(f"  {msg}")
        notify_scrape_status(f"❌ {msg}")
        # Mark complete so no later trigger retries it even later.
        open(done_marker, "w").close()
        return
    jitter = random.randint(0, min(480, int(lead_s)))
    if jitter:
        print(f"  [jitter] Waiting {jitter}s before scrape...")
        time.sleep(jitter)

    try:
        pre_rows = run_snapshot(event, url, "pre_game", pg_csv, team["slug"])
        supabase_client.insert_listings(game_id, pre_rows, "pre_game", team["slug"], game_dt, league="nfl")
    except Exception as e:
        notify_scrape_status(f"❌ {game_label}: NOT scraped. Pre-game scrape failed: {e}")
        raise

    print("\nWaiting for halftime...")
    wait_for_halftime(kickoff, team["espn_tricode"])
    if os.path.isfile(done_marker):
        print(f"  {team_label} vs {opponent} ({game_dt}) was completed by another "
              f"run while this one was waiting - skipping.")
        return

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
    except Exception as e:
        notify_scrape_status(f"❌ {game_label}: NOT scraped. Halftime scrape failed: {e}")
        raise

    print("\nComparing snapshots...")
    # save_csv() legitimately writes nothing when a scrape returns zero rows
    # (confirmed live, 2026-09-09: the halftime scrape got back only 4 real
    # seats from TM, well below the threshold below, and never even got that
    # far because this used to crash on the missing file first) -- a missing
    # halftime.csv is a valid, expected state, not an error. The same is
    # true of pre_game.csv, which this line used to load unconditionally:
    # confirmed live 2026-09-20/21/22 (runs 35531541265, 35549121946,
    # 35675895765) -- a pre-game scrape that legitimately found 0 listings
    # (TM bot-pressure during a 13-game Sunday, or just an empty market)
    # never wrote pre_game.csv, and the game still proceeded through the
    # halftime wait/scrape only to crash here with an unhandled
    # FileNotFoundError instead of completing with zero pre-game rows.
    pre_rows = load_csv(pg_csv) if os.path.isfile(pg_csv) else []
    ht_rows = load_csv(ht_csv) if os.path.isfile(ht_csv) else []

    # See evaluate_snapshot_quality() for what each verdict means and the
    # real incidents behind each gate.
    evaluation = evaluate_snapshot_quality(pre_rows, ht_rows)

    if evaluation.verdict == "no_pregame":
        notify_scrape_status(f"❌ {game_label}: NOT scraped. Pre-game scrape found 0 listings "
                             f"(page off-sale or blocked). Nothing recorded.")
        Path(done_marker).touch()
        return

    if evaluation.verdict == "marketplace_collapsed":
        msg = (f"Halftime listings collapsed to {len(ht_rows)} from {len(pre_rows)} pre-game "
               f"(below {MIN_HALFTIME_RATIO:.0%}) -- likely TM's marketplace closing out "
               f"listings, not real no-shows. Skipping no-show insert to avoid recording "
               f"fabricated data.")
        print(f"  {msg}")
        notify_scrape_status(f"⚠️ {game_label}: scraped ({len(pre_rows)} pre-game, "
                             f"{len(ht_rows)} halftime) but no-shows NOT recorded. {msg}")
        Path(done_marker).touch()
        return

    no_shows = evaluation.no_shows

    if evaluation.verdict == "impossible_no_shows":
        msg = (f"{len(no_shows)} no-shows exceeds the smaller snapshot "
               f"(pre-game {len(pre_rows)}, halftime {len(ht_rows)}) - "
               f"mathematically impossible for a real intersection, almost "
               f"certainly two mismatched/duplicate snapshots. Skipping "
               f"no-show insert to avoid recording corrupted data.")
        print(f"  {msg}")
        notify_scrape_status(f"⚠️ {game_label}: scraped ({len(pre_rows)} pre-game, "
                             f"{len(ht_rows)} halftime) but no-shows NOT recorded. {msg}")
        Path(done_marker).touch()
        return

    save_no_shows(no_shows, ns_csv)
    supabase_client.insert_no_shows(game_id, no_shows, team["slug"], game_dt, league="nfl")
    print_report(pre_rows, ht_rows, no_shows, ns_csv)
    notify_scrape_status(f"✅ {game_label}: scraped. {len(pre_rows):,} seats listed pre-game, "
                         f"{len(ht_rows):,} at halftime, {len(no_shows):,} no-shows recorded.")
    Path(done_marker).touch()


if __name__ == "__main__":
    main()
