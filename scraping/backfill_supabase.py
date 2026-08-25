"""
backfill_supabase.py

One-time script: reads all existing NBA CSV data and loads it into Supabase.
Safe to re-run — games use upsert (no duplicates), but listings/no_shows
are inserted fresh each run so run only once (or wipe the tables first).

Usage:
    python3 backfill_supabase.py
    python3 backfill_supabase.py --dry-run   # preview only, no writes
"""

import csv
import json
import os
import sys

DRY_RUN = "--dry-run" in sys.argv

if not DRY_RUN:
    import supabase_client

DATA_ROOT = "data"

def load_csv(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def find_game_folders() -> list[tuple[str, str]]:
    """
    Walk data/<team>/<game_folder>/ and return (team_slug, folder_path)
    for every folder that contains a game_meta.json.
    Skips data/mlb/ (MLB data handled separately).
    """
    results = []
    for team_slug in sorted(os.listdir(DATA_ROOT)):
        team_path = os.path.join(DATA_ROOT, team_slug)
        if not os.path.isdir(team_path) or team_slug == "mlb":
            continue
        for game_folder in sorted(os.listdir(team_path)):
            game_path = os.path.join(team_path, team_slug, game_folder)
            # game folders are directly under team_path
            game_path = os.path.join(team_path, game_folder)
            if not os.path.isdir(game_path):
                continue
            meta_path = os.path.join(game_path, "game_meta.json")
            if os.path.isfile(meta_path):
                results.append((team_slug, game_path))
    return results


def main():
    folders = find_game_folders()
    print(f"Found {len(folders)} game folders with metadata.\n")

    total_games = 0
    total_listings = 0
    total_no_shows = 0
    skipped = 0

    for team_slug, gdir in folders:
        meta_path   = os.path.join(gdir, "game_meta.json")
        pg_path     = os.path.join(gdir, "pre_game.csv")
        ht_path     = os.path.join(gdir, "halftime.csv")
        ns_path     = os.path.join(gdir, "no_shows.csv")

        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)

        game_date = meta.get("game_date", "")
        if not game_date:
            print(f"  [SKIP] {gdir} — no game_date in meta")
            skipped += 1
            continue

        pre_rows = load_csv(pg_path)
        ht_rows  = load_csv(ht_path)
        ns_rows  = load_csv(ns_path)

        label = f"{team_slug} {game_date}"
        print(f"  {label}: pre={len(pre_rows)} ht={len(ht_rows)} no_shows={len(ns_rows)}", end="")

        if DRY_RUN:
            print("  [dry-run]")
            total_games    += 1
            total_listings += len(pre_rows) + len(ht_rows)
            total_no_shows += len(ns_rows)
            continue

        # Upsert game record
        game_id = supabase_client.upsert_game(meta, league="nba")

        if game_id is None:
            print("  → upsert failed, skipping listings")
            skipped += 1
            continue

        # Insert listings
        if pre_rows:
            supabase_client.insert_listings(game_id, pre_rows, "pre_game", team_slug, game_date)
        if ht_rows:
            supabase_client.insert_listings(game_id, ht_rows, "halftime", team_slug, game_date)
        if ns_rows:
            supabase_client.insert_no_shows(game_id, ns_rows, team_slug, game_date)

        print(f"  → game_id={game_id}")
        total_games    += 1
        total_listings += len(pre_rows) + len(ht_rows)
        total_no_shows += len(ns_rows)

    print(f"\n{'[DRY RUN] ' if DRY_RUN else ''}Done.")
    print(f"  Games:     {total_games}")
    print(f"  Listings:  {total_listings}")
    print(f"  No-shows:  {total_no_shows}")
    if skipped:
        print(f"  Skipped:   {skipped}")


if __name__ == "__main__":
    main()
