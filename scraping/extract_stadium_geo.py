"""
extract_stadium_geo.py

Turns a captured data/{slug}/seatmap_geo.json into the compact JSON a
seat-map page needs: every seat's real position (grouped by section), plus
each section's tier and centroid for the overview map. Some seats within a
curated set of sections are marked "open now" (simulated — see note below)
so the demo has something to highlight.

Usage:
    python3 extract_stadium_geo.py <team_slug>

Output:
    data/{slug}/seatmap_extract.json
"""

import json
import os
import random
import sys

random.seed(49)


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

    # Pick a representative spread of standard sections to simulate "open
    # now" seats for — we don't have real no-show data yet (these are
    # upcoming games), so this is illustrative, same as the 49ers page.
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

    out = {"tiers": tiers, "centroids": centroids, "secs": secs}
    out_path = f"data/{slug}/seatmap_extract.json"
    with open(out_path, "w") as f:
        json.dump(out, f, separators=(",", ":"))

    sz = os.path.getsize(out_path)
    print(f"[{slug}] sections: {len(secs)}  seats: {total_seats}  open: {total_open}  "
          f"-> {out_path} ({sz/1024/1024:.2f} MB)")


if __name__ == "__main__":
    main()
