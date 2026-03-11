"""
compare_snapshots.py

Reads pre_game.csv and halftime.csv for a game and identifies confirmed no-show seats.

A listing present in BOTH snapshots = seat that never sold = empty at halftime.

Usage:
    python compare_snapshots.py <team_slug>

    e.g.  python compare_snapshots.py warriors
          python compare_snapshots.py lakers
"""

import csv
import os
import sys

from teams import get_team, pre_game_csv, halftime_csv, no_shows_csv, game_dir


def load_csv(path: str) -> list[dict]:
    """Read all rows from a CSV file."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{path} not found.")
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def compare(pre_game: list[dict], halftime: list[dict]) -> list[dict]:
    """Return rows whose offer_id appears in both snapshots = confirmed no-shows."""
    pre_ids      = {r["offer_id"]: r for r in pre_game if r["offer_id"]}
    halftime_ids = {r["offer_id"] for r in halftime if r["offer_id"]}

    no_shows = [
        row for offer_id, row in pre_ids.items()
        if offer_id in halftime_ids
    ]
    # Sort by section then row for readability
    no_shows.sort(key=lambda r: (r.get("section", ""), r.get("row", "")))
    return no_shows


def save_no_shows(rows: list[dict], path: str) -> None:
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def print_report(pre_game: list[dict], halftime: list[dict], no_shows: list[dict], out_csv: str) -> None:
    sold = len({r["offer_id"] for r in pre_game if r["offer_id"]}) - len(no_shows)

    print()
    print("=" * 54)
    print("  NO-SHOW SEAT REPORT")
    print("=" * 54)
    print(f"  Pre-game listings:   {len(pre_game)}")
    print(f"  Halftime listings:   {len(halftime)}")
    print(f"  Sold between scans:  {sold}")
    print(f"  Confirmed no-shows:  {len(no_shows)}")

    if no_shows:
        prices = [float(r["price_usd"]) for r in no_shows if r.get("price_usd")]
        if prices:
            total_face = sum(prices)
            print(f"  Total face value:   ${total_face:,.0f}")
            print(f"  Avg no-show price:  ${total_face / len(prices):,.0f}")

        print()
        print("  Sample no-show listings (first 10):")
        for r in no_shows[:10]:
            price = f"${float(r['price_usd']):.0f}" if r.get("price_usd") else "N/A"
            print(f"    Section {r['section']:<8} {r.get('selection_type',''):<8} {price}")

    print()
    print(f"  Full list saved to: {out_csv}")
    print("=" * 54)


def main():
    if len(sys.argv) != 3:
        print("Usage: python compare_snapshots.py <team_slug> <game_folder>")
        print("  e.g. python compare_snapshots.py magic 2026-03-11_cleveland_cavaliers_at_magic")
        sys.exit(1)

    team   = get_team(sys.argv[1])
    gdir   = f"data/{team['slug']}/{sys.argv[2]}"
    pg     = pre_game_csv(gdir)
    ht     = halftime_csv(gdir)
    out    = no_shows_csv(gdir)

    print(f"Loading pre_game:  {pg}")
    print(f"Loading halftime:  {ht}")
    pre_rows = load_csv(pg)
    ht_rows  = load_csv(ht)

    no_shows = compare(pre_rows, ht_rows)
    save_no_shows(no_shows, out)
    print_report(pre_rows, ht_rows, no_shows, out)


if __name__ == "__main__":
    main()
