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


def convex_hull(points):
    """
    Andrew's monotone chain convex hull. Returns hull points in CCW order.
    Used to draw each section's own clickable/colorable shape directly from
    its real seat coordinates -- Ticketmaster's background art turned out
    to have no shape at all for a large share of real sections (confirmed
    live: only 168/260 for one venue even after chasing every id-naming
    variant we could find), so this sidesteps that entirely by never
    depending on their art having a matching shape in the first place.
    """
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    return lower[:-1] + upper[:-1]


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
    hulls = {}
    total_seats = 0
    total_open = 0

    # How far to push each hull point out from the section's own centroid,
    # so the drawn shape has some real margin around the seats instead of
    # a razor-thin outline hugging the exact dot positions -- closer to
    # how TM's own clean section blocks look.
    HULL_PAD = 1.18

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
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            centroids[name] = [round(cx, 1), round(cy, 1)]

            hull = convex_hull(list(zip(xs, ys)))
            if len(hull) < 3:
                # All seats collinear (a single-row section, e.g. a narrow
                # accessible-seating row) -- convex_hull degenerates to a
                # line with no fillable interior. Thicken it into a thin
                # rectangle perpendicular to the row instead of dropping
                # the section entirely.
                (x0, y0), (x1, y1) = (hull[0], hull[-1]) if len(hull) == 2 else (hull[0], hull[0])
                dx, dy = x1 - x0, y1 - y0
                length = (dx * dx + dy * dy) ** 0.5
                if length < 1e-6:
                    nx, ny = 1.0, 0.0
                else:
                    nx, ny = -dy / length, dx / length
                half_w = 12.0
                hull = [
                    (x0 + nx * half_w, y0 + ny * half_w),
                    (x1 + nx * half_w, y1 + ny * half_w),
                    (x1 - nx * half_w, y1 - ny * half_w),
                    (x0 - nx * half_w, y0 - ny * half_w),
                ]
            padded = [
                [round(cx + (hx - cx) * HULL_PAD, 1), round(cy + (hy - cy) * HULL_PAD, 1)]
                for hx, hy in hull
            ]
            hulls[name] = padded

    out = {
        "tiers": tiers,
        "centroids": centroids,
        "hulls": hulls,
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
