"""
check_data_integrity.py

Scans Supabase for no-show records that violate a hard invariant: a no-show
is defined as a seat present in BOTH the pre-game and halftime snapshots, so
its count can never exceed the smaller of the two raw snapshot counts. This
isn't a heuristic like nfl_run_game.py's MIN_HALFTIME_RATIO gate (a judgment
call about market behavior) - it's mathematically impossible for a genuine
intersection, so any row that violates it is corrupted data, full stop.

Confirmed root cause of the corruption this catches: a 2026-09-20 git-push
failure (since fixed) let two mismatched snapshots from different runs get
compared against each other, inserting no_shows counts far exceeding either
snapshot. nfl_run_game.py now gates against this at insert time, but this
script exists to (a) clean up rows that were corrupted before that fix
existed, and (b) catch any future instance of the same class of bug that
slips past the insert-time gate some other way.

Usage:
    python check_data_integrity.py [--league nfl] [--dry-run]

Deletes the corrupted no_shows rows for any game that fails the invariant
(never touches the underlying listings/games rows, which are raw scrape
records, not derived data) and prints a report. With --dry-run, only
reports, changes nothing.

Deliberately queries per-game, not with a single bulk fetch across the
whole league: an early version tried to page through the entire listings
table at once and hit two real Postgres/PostgREST issues - unstable row
ordering across paginated OFFSET calls without an explicit sort, and then
a statement timeout even with cursor pagination once narrowed to one big
unfiltered scan. Reusing supabase_client's per-game count="exact" pattern
(same one count_listings()/count_no_shows() already use successfully)
sidesteps both: each query is small, indexed, and fast regardless of how
large the tables get overall.
"""

import argparse
import sys

sys.path.insert(0, ".")
from supabase_client import _get_client  # noqa: E402


def count_snapshot(client, game_id: int, league: str, snapshot: str) -> int:
    result = (
        client.table("listings")
        .select("id", count="exact")
        .eq("game_id", game_id)
        .eq("league", league)
        .eq("snapshot", snapshot)
        .limit(1)
        .execute()
    )
    return result.count or 0


def fetch_no_show_ids(client, game_id: int, league: str) -> list[int]:
    out = []
    page = 1000
    last_id = -1
    while True:
        rows = (
            client.table("no_shows")
            .select("id")
            .eq("game_id", game_id)
            .eq("league", league)
            .gt("id", last_id)
            .order("id")
            .limit(page)
            .execute()
        ).data
        if not rows:
            break
        out.extend(r["id"] for r in rows)
        last_id = rows[-1]["id"]
        if len(rows) < page:
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--league", default="nfl")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    client = _get_client()
    if client is None:
        print("Supabase not configured (SUPABASE_URL/SUPABASE_SERVICE_KEY missing) - nothing to check.")
        return 0

    games = (
        client.table("games")
        .select("id,home_team,opponent,game_date")
        .eq("league", args.league)
        .execute()
    ).data

    corrupted = []
    for g in games:
        gid = g["id"]
        pre = count_snapshot(client, gid, args.league, "pre_game")
        half = count_snapshot(client, gid, args.league, "halftime")
        if pre == 0 or half == 0:
            continue  # can't evaluate the invariant without both snapshots
        ns_count = (
            client.table("no_shows")
            .select("id", count="exact")
            .eq("game_id", gid)
            .eq("league", args.league)
            .limit(1)
            .execute()
        ).count or 0
        if ns_count > min(pre, half):
            corrupted.append({
                "game_id": gid,
                "home_team": g.get("home_team", "?"),
                "opponent": g.get("opponent", "?"),
                "game_date": g.get("game_date", "?"),
                "pre_game": pre,
                "halftime": half,
                "no_shows": ns_count,
            })

    if not corrupted:
        print(f"No corrupted no_shows rows found for league={args.league}. Clean.")
        return 0

    print(f"Found {len(corrupted)} game(s) with impossible no_shows counts:\n")
    for c in corrupted:
        print(f"  game {c['game_id']}: {c['home_team']} vs {c['opponent']} ({c['game_date']}) - "
              f"pre_game={c['pre_game']} halftime={c['halftime']} no_shows={c['no_shows']} "
              f"(max possible: {min(c['pre_game'], c['halftime'])})")

    if args.dry_run:
        print("\n--dry-run: not deleting anything.")
        return 1

    print()
    for c in corrupted:
        ids = fetch_no_show_ids(client, c["game_id"], args.league)
        # Batch deletes: a single .in_() with thousands of ids blows past
        # PostgREST's request size limit ("JSON could not be generated" /
        # 400 Bad Request) - confirmed live on a game with ~3700 rows.
        for i in range(0, len(ids), 200):
            client.table("no_shows").delete().in_("id", ids[i:i + 200]).execute()
        print(f"  Deleted {len(ids)} corrupted no_shows rows for game {c['game_id']}.")

    return 1  # non-zero so the workflow step (and thus the issue-filing step) knows something was found


if __name__ == "__main__":
    sys.exit(main())
