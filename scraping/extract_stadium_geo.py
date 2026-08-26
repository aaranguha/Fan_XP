"""
extract_stadium_geo.py

Turns a captured data/{slug}/seatmap_geo.json into the compact JSON a
seat-map page needs: every seat's real position (grouped by section), plus
each section's tier and centroid for the overview map.

"Open now" seats come from real confirmed no-shows (data/nfl/{slug}/*/
no_shows.csv, written by nfl_runner.py once a home game has actually been
played) when available. Until a team's first home game happens, there's
nothing real to show yet, so a representative spread of seats is marked
open instead — clearly labeled as a preview in the output so the frontend
can be honest about it, never silently passed off as real.

Usage:
    python3 extract_stadium_geo.py <team_slug>

Output:
    data/{slug}/seatmap_extract.json
"""

import csv
import glob
import json
import os
import random
import sys

random.seed(49)


def normalize(s: str) -> str:
    return (s or "").strip().upper()


def find_real_no_shows(slug: str):
    """
    Returns (game_date, {(section, row, seat), ...}) from the most recent
    completed game's no_shows.csv, or (None, None) if the team hasn't had
    a real home game scraped yet.
    """
    game_dirs = sorted(glob.glob(f"data/nfl/{slug}/*/no_shows.csv"))
    if not game_dirs:
        return None, None

    latest = game_dirs[-1]  # folder names are date-prefixed, so sort = chronological
    game_date = os.path.basename(os.path.dirname(latest)).split("_", 1)[0]

    seats = set()
    with open(latest, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            seats.add((normalize(row.get("section")), normalize(row.get("row")), normalize(row.get("seat"))))
    return game_date, seats


def tier_of(name: str) -> str:
    if name.startswith("C"):
        return "club"
    if name.startswith("P"):
        return "suite"
    if "VIP" in name:
        return "suite"
    if "FLD" in name or name.startswith("SR"):
        return "field"
    digits = "".join(c for c in name if c.isdigit())
    if not digits:
        return "other"
    n = int(digits)
    if 100 <= n < 200:
        return "lower"
    if 200 <= n < 300:
        return "mezz"
    if 300 <= n < 500:
        return "upper"
    return "other"


def find_sections(seg, out):
    if seg.get("segmentCategory", "") == "SECTION":
        out.append(seg)
    for child in seg.get("segments", []):
        find_sections(child, out)


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 extract_stadium_geo.py <team_slug>")
        sys.exit(1)
    slug = sys.argv[1]

    geo_path = f"data/{slug}/seatmap_geo.json"
    if not os.path.isfile(geo_path):
        print(f"ERROR: {geo_path} not found. Run fetch_geometry.py for {slug} first.")
        sys.exit(1)

    d = json.load(open(geo_path))
    page = d.get("pages", [d])[0]

    sections = []
    for seg in page.get("segments", []):
        find_sections(seg, sections)

    game_date, real_no_shows = find_real_no_shows(slug)
    using_real_data = real_no_shows is not None

    if not using_real_data:
        # No completed home game scraped yet for this team — mark a
        # representative spread of standard sections open instead, purely
        # so the preview has something to highlight.
        lower_named = sorted(
            (s["name"] for s in sections if tier_of(s["name"]) == "lower"),
        )
        open_sections = set(lower_named[::4][:14])  # every 4th lower section, up to 14

    secs = {}
    tiers = {}
    centroids = {}
    total_seats = 0
    total_open = 0

    for sec in sections:
        name = sec["name"]
        tier = tier_of(name)
        tiers[name] = tier

        dots = []
        xs, ys = [], []
        for row in sec.get("segments", []):
            row_label = row.get("name", "")
            for p in row.get("placesNoKeys", []):
                if len(p) < 4:
                    continue
                seat_num, x, y = p[1], p[2], p[3]
                level = p[4] if len(p) > 4 else None
                if using_real_data:
                    is_open = (normalize(name), normalize(row_label), normalize(str(seat_num))) in real_no_shows
                else:
                    is_open = name in open_sections and random.random() < 0.22
                dots.append([round(x, 1), round(y, 1), row_label, seat_num, level, is_open])
                xs.append(x)
                ys.append(y)
                total_seats += 1
                if is_open:
                    total_open += 1

        if dots:
            secs[name] = dots
            centroids[name] = [round(sum(xs) / len(xs), 1), round(sum(ys) / len(ys), 1)]

    out = {
        "tiers": tiers,
        "centroids": centroids,
        "secs": secs,
        "data_source": "real" if using_real_data else "simulated",
        "game_date": game_date,
    }
    out_path = f"data/{slug}/seatmap_extract.json"
    with open(out_path, "w") as f:
        json.dump(out, f, separators=(",", ":"))

    sz = os.path.getsize(out_path)
    source_label = f"REAL ({game_date})" if using_real_data else "simulated"
    print(f"[{slug}] sections: {len(secs)}  seats: {total_seats}  open: {total_open}  source: {source_label}  "
          f"-> {out_path} ({sz/1024/1024:.2f} MB)")


if __name__ == "__main__":
    main()
