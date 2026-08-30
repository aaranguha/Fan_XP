"""
generate_team_story.py  —  generalized TM-style seat visualization

Requires geometry files in data/{team}/:
  arena.svg        — DOM-extracted SVG with data-section-name attributes (section hit areas)
  arena_full.svg   — full network SVG used as darkened background
  seatmap_geo.json — per-seat coordinates from TM placeDetailNoKeys API

Usage:
    python generate_team_story.py <team_slug>
    python generate_team_story.py all          # regenerate every team

    Outputs: docs/{team}_seat_story.html
"""
import csv, json, os, re, sys
from collections import defaultdict
from datetime import datetime

# ── Team branding ──────────────────────────────────────────────────────────────
TEAM_META = {
    "bulls":        {"name": "Bulls",        "arena": "United Center",              "color": "#ce1141"},
    "magic":        {"name": "Magic",        "arena": "Kia Center",                 "color": "#0077c0"},
    "celtics":      {"name": "Celtics",      "arena": "TD Garden",                  "color": "#007a33"},
    "lakers":       {"name": "Lakers",       "arena": "Crypto.com Arena",           "color": "#552583"},
    "warriors":     {"name": "Warriors",     "arena": "Chase Center",               "color": "#1d428a", "bold_arena": True},
    "knicks":       {"name": "Knicks",       "arena": "Madison Square Garden",      "color": "#006bb6"},
    "heat":         {"name": "Heat",         "arena": "Kaseya Center",              "color": "#98002e"},
    "bucks":        {"name": "Bucks",        "arena": "Fiserv Forum",               "color": "#00471b"},
    "nets":         {"name": "Nets",         "arena": "Barclays Center",            "color": "#ffffff"},
    "76ers":        {"name": "76ers",        "arena": "Wells Fargo Center",         "color": "#006bb6"},
    "clippers":     {"name": "Clippers",     "arena": "Intuit Dome",                "color": "#c8102e"},
    "mavericks":    {"name": "Mavericks",    "arena": "American Airlines Center",   "color": "#00538c"},
    "nuggets":      {"name": "Nuggets",      "arena": "Ball Arena",                 "color": "#fec524"},
    "pacers":       {"name": "Pacers",       "arena": "Gainbridge Fieldhouse",      "color": "#002d62"},
    "pistons":      {"name": "Pistons",      "arena": "Little Caesars Arena",       "color": "#c8102e"},
    "raptors":      {"name": "Raptors",      "arena": "Scotiabank Arena",           "color": "#ce1141"},
    "rockets":      {"name": "Rockets",      "arena": "Toyota Center",              "color": "#ce1141"},
    "grizzlies":    {"name": "Grizzlies",    "arena": "FedExForum",                 "color": "#5d76a9"},
    "hawks":        {"name": "Hawks",        "arena": "State Farm Arena",           "color": "#e03a3e"},
    "hornets":      {"name": "Hornets",      "arena": "Spectrum Center",            "color": "#1d1160"},
    "kings":        {"name": "Kings",        "arena": "Golden 1 Center",            "color": "#5a2d81"},
    "spurs":        {"name": "Spurs",        "arena": "Frost Bank Center",          "color": "#c4ced4"},
    "suns":         {"name": "Suns",         "arena": "Footprint Center",           "color": "#1d1160"},
    "thunder":      {"name": "Thunder",      "arena": "Paycom Center",              "color": "#007ac1"},
    "timberwolves": {"name": "Timberwolves", "arena": "Target Center",              "color": "#0c2340"},
    "blazers":      {"name": "Blazers",      "arena": "Moda Center",                "color": "#e03a3e"},
    "pelicans":     {"name": "Pelicans",     "arena": "Smoothie King Center",       "color": "#0c2340"},
    "cavaliers":    {"name": "Cavaliers",    "arena": "Rocket Mortgage FieldHouse", "color": "#860038"},
    "jazz":         {"name": "Jazz",         "arena": "Delta Center",               "color": "#002b5c"},
    "wizards":      {"name": "Wizards",      "arena": "Capital One Arena",          "color": "#002b5c"},
}

def _rate_color(r):
    """Green -> amber -> red gradient by no-show rate 0..1 (matches WNBA generator)."""
    r = max(0.0, min(1.0, r))
    if r <= 0.5:
        t = r * 2
        return f"#{int(t*255):02x}{int(229-t*14):02x}{int(160-t*160):02x}"
    else:
        t = (r - 0.5) * 2
        return f"#ff{int(215-t*138):02x}{int(t*109):02x}"


SVG_W, SVG_H = 960, 720
TM_W,  TM_H  = 10240.0, 7680.0
SX = SVG_W / TM_W
SY = SVG_H / TM_H


# ── Data helpers ───────────────────────────────────────────────────────────────

def find_all_games(team_slug):
    """Return all game folders with pre_game.csv, sorted oldest→newest."""
    team_dir = f"data/{team_slug}"
    games = []
    for folder in sorted(os.listdir(team_dir)):
        gdir = os.path.join(team_dir, folder)
        if not os.path.isdir(gdir) or not folder.startswith("202"):
            continue
        if os.path.isfile(f"{gdir}/pre_game.csv"):
            games.append(folder)
    return games


def load_game_keys(game_dir):
    """Load ns_keys, pre_keys, pre_price for one game."""
    ns_keys   = set()
    pre_keys  = set()
    pre_price = {}

    pre_path = f"{game_dir}/pre_game.csv"
    if os.path.isfile(pre_path):
        with open(pre_path, newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if "row" not in r or "seat" not in r:
                    # offer-level schema (no row/seat) — skip seat-level tracking
                    break
                sec, row, seat = r["section"].strip(), r["row"].strip(), r["seat"].strip()
                key = (sec, row, seat)
                pre_keys.add(key)
                try:    pre_price[key] = round(float(r["price_usd"]))
                except: pre_price[key] = 0

    ns_path = f"{game_dir}/no_shows.csv"
    if os.path.isfile(ns_path):
        with open(ns_path, newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if "row" not in r or "seat" not in r:
                    break
                ns_keys.add((r["section"].strip(), r["row"].strip(), r["seat"].strip()))

    return ns_keys, pre_keys, pre_price


def load_game_meta(game_dir):
    meta_path = f"{game_dir}/game_meta.json"
    if os.path.isfile(meta_path):
        with open(meta_path) as f:
            return json.load(f)
    return {}


def dropdown_label(folder, meta):
    """Short label for dropdown: '4/9 · Lakers'"""
    opp  = meta.get("opponent", "")
    date = meta.get("game_date", "")
    if not date:
        date = folder[:10]
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
        date_str = d.strftime("%-m/%-d")
    except Exception:
        date_str = date
    if opp:
        # Strip any parenthetical suffix e.g. "Charlotte Hornets (Jayson Tatum Bobblehead*)"
        opp_clean = opp.split("(")[0].strip()
        opp_short = opp_clean.split()[-1] if opp_clean else "?"
    else:
        # Parse from folder name e.g. 2026-04-07_sacramento_kings_at_warriors
        if "_at_" in folder:
            opp_short = folder.split("_at_")[0].split("_")[-1].title()
        else:
            opp_short = "?"
    return f"{date_str} · {opp_short}"


def story_label(folder, meta):
    """Longer label for story: 'vs. Los Angeles Lakers · Apr 9, 2026'"""
    opp  = meta.get("opponent", "")
    date = meta.get("game_date", folder[:10])
    try:
        d = datetime.strptime(date, "%Y-%m-%d")
        date_str = d.strftime("%b %-d, %Y")
    except Exception:
        date_str = date
    if opp:
        return f"vs. {opp} · {date_str}"
    return date_str


def build_games_data(team_slug, game_folders):
    """Return list of game dicts with all JS-needed data."""
    games = []
    for folder in game_folders:
        gdir = f"data/{team_slug}/{folder}"
        meta = load_game_meta(gdir)
        ns_keys, pre_keys, pre_price = load_game_keys(gdir)
        if not pre_keys:
            # offer-level CSV only — no seat-level data, skip this game
            continue

        # Per-section stats
        sec_pre = defaultdict(int)
        sec_ns  = defaultdict(int)
        for (sec, row, seat) in pre_keys:
            sec_pre[sec] += 1
        for (sec, row, seat) in ns_keys:
            sec_ns[sec] += 1

        total_pre = len(pre_keys)
        total_ns  = len(ns_keys)
        rate      = round(total_ns / total_pre, 4) if total_pre else 0

        # Dead $ estimate
        dead = sum(pre_price.get(k, 0) for k in ns_keys) if ns_keys else 0
        CONCESSION_PER_SEAT = 35
        phantom = dead + total_ns * CONCESSION_PER_SEAT

        # Top sections for bar chart (top 7 by ns, fallback to pre)
        sec_counts = dict(sec_ns) if total_ns > 0 else dict(sec_pre)
        top_secs   = sorted(sec_counts.items(), key=lambda x: x[1], reverse=True)[:7]

        # Story text fields
        total_listed_value = sum(pre_price.get(k, 0) for k in pre_keys)
        if total_ns > 0:
            avg_price = round(dead / total_ns) if total_ns else 0
            concession_lost = total_ns * CONCESSION_PER_SEAT
            story_headline = f"${phantom:,.0f} in phantom revenue."
            story_headline_span = f"${phantom:,.0f}"
            story_sub = (f"{total_ns:,} seats listed pre-game were gone by halftime — "
                         f"<strong>${dead:,.0f}</strong> in dead seat value "
                         f"(avg ${avg_price:,}/seat) plus <strong>${concession_lost:,}</strong> in "
                         f"estimated concession spend ($35/seat). "
                         f"That's <strong>${phantom:,.0f} in phantom revenue</strong> from a single game.")
            chart_note = "Every listed seat in these sections was empty by halftime."
            chart_title_prefix = "Top sections by no-shows"
        else:
            story_headline = f"{total_pre:,} seats listed on the secondary market."
            story_headline_span = str(total_pre)
            story_sub = (f"These are all seats listed on Ticketmaster's secondary market before tip-off "
                         f"for this game — real inventory that fans paid for but may not show up to use.")
            chart_note = "Top sections by secondary market listings pre-game."
            chart_title_prefix = "Top sections pre-game"

        games.append({
            "folder":   folder,
            "meta":     meta,
            "label":    dropdown_label(folder, meta),
            "story":    story_label(folder, meta),
            "opp":      meta.get("opponent", ""),
            "date":     meta.get("game_date", folder[:10]),
            "pre":      total_pre,
            "ns":       total_ns,
            "rate":     rate,
            "dead":     dead,
            "phantom":  phantom,
            "ns_keys":  list(ns_keys),
            "pre_keys": list(pre_keys),
            "pre_price":pre_price,
            "sec_pre":  dict(sec_pre),
            "sec_ns":   dict(sec_ns),
            "topSecs":  top_secs,
            "headlineSpan": story_headline_span,
            "headlineRest": story_headline[len(story_headline_span):].strip(),
            "sub":      story_sub,
            "chartNote": chart_note,
            "chartTitlePrefix": chart_title_prefix,
        })
    return games


# ── Geometry helpers ───────────────────────────────────────────────────────────

def load_section_paths(dom_svg):
    if not dom_svg or not os.path.isfile(dom_svg):
        return {}
    with open(dom_svg) as f:
        content = f.read()
    paths = re.findall(r'<path([^>]*data-section-name[^>]*)/?>', content, re.DOTALL)
    out = {}
    for p in paths:
        nm = re.search(r'data-section-name="([^"]+)"', p)
        d  = re.search(r'\bd="([^"]+)"', p)
        if nm and d:
            out[nm.group(1)] = d.group(1)
    return out


def _find_group_end(content, start):
    pos = start + content.index('>', start) + 1
    depth = 1
    while pos < len(content) and depth > 0:
        o = content.find('<g', pos)
        c = content.find('</g>', pos)
        if c == -1:
            break
        if o != -1 and o < c:
            depth += 1; pos = o + 2
        else:
            depth -= 1
            if depth == 0:
                return c + 4
            pos = c + 4
    return len(content)


def load_bg_parts(bg_svg, fallback_svg=None):
    path = bg_svg if os.path.isfile(bg_svg) else fallback_svg
    if not path or not os.path.isfile(path):
        return "", ""
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    inner = re.sub(r'^<\?xml[^>]*\?>\s*', '', raw)
    inner = re.sub(r'^<svg[^>]*>', '', inner, count=1).rstrip()
    if inner.endswith("</svg>"):
        inner = inner[:-6]
    court_group = ""
    field_open_tag = '<g id="field">'
    field_start = inner.find(field_open_tag)
    if field_start != -1:
        field_end  = _find_group_end(inner, field_start)
        field_html = inner[field_start:field_end]
        if '<image' in field_html:
            img_m = re.search(r'(<image\b)([^/]*)(/>)', field_html, re.DOTALL)
            if img_m:
                img_tag = img_m.group(0)
                if ' y=' not in img_tag:
                    path_ys = [float(m.group(1)) for m in re.finditer(r'M[\d.]+[, ]([\d.]+)', field_html)]
                    court_y = min(path_ys) if path_ys else 3346.0
                    field_html = (field_html[:img_m.start()] +
                                  img_m.group(1) + f' y="{court_y:.2f}"' + img_m.group(2) + img_m.group(3) +
                                  field_html[img_m.end():])
        court_group = field_html
        inner = inner[:field_start] + inner[field_end:]
    return inner, court_group


def load_geo_multi(geo_path, all_pre_keys, all_pre_price):
    """
    Load geometry using the union of all pre_keys across all games.
    Pack row/seat for every seat that ever appeared in any game's pre_game.csv.
    """
    with open(geo_path) as f:
        geo = json.load(f)
    page = geo.get("pages", [geo])[0]
    sections = {}

    def collect_places(node, sec_name, row_name, dots):
        for p in node.get("placesNoKeys", []):
            if len(p) >= 4:
                key = (sec_name, row_name, str(p[1]))
                x   = round(p[2] * SX, 2)
                y   = round(p[3] * SY, 2)
                if key in all_pre_keys:
                    price = all_pre_price.get(key, 0)
                    dots.append([x, y, 1, row_name, str(p[1]), price])
                else:
                    dots.append([x, y, 0])
        for child in node.get("segments", []):
            collect_places(child, sec_name, child.get("name", row_name), dots)

    def extract_composite(seg):
        name = seg.get("name", "")
        dots = []
        for child in seg.get("segments", []):
            collect_places(child, name, child.get("name", ""), dots)
        if dots:
            sections[name] = dots

    for seg in page.get("segments", []):
        if seg.get("segmentCategory") == "COMPOSITE":
            extract_composite(seg)
        for child in seg.get("segments", []):
            if child.get("segmentCategory") == "COMPOSITE":
                extract_composite(child)

    return sections


# ── (removed) Story HTML builder ───────────────────────────────────────────────
# The below-the-fold narrative "story" section (scroll-snap essay, bar charts,
# comparison table) was removed — that data now lives in the dedicated
# nba_{slug}_dashboard.html KPI pages. This file now renders a single clean
# section-fill arena map + stat bar, matching the NFL seatmap pages.


# ── Main HTML generator ────────────────────────────────────────────────────────

def gen_html(team_slug, games_data, sec_paths, geo_sections, bg_inner, court_img):
    meta  = TEAM_META.get(team_slug, {"name": team_slug.title(), "arena": "", "color": "#ce1141"})
    color = meta["color"]
    name  = meta["name"]
    arena = meta["arena"]

    sec_paths_js = json.dumps(sec_paths, separators=(',', ':'))

    # Pack geo_sections down to just a bounding box per section (in already-
    # scaled render-space coordinates). We no longer render individual seat
    # dots, so per-seat coordinates/row/seat/price are not needed client-side —
    # only enough geometry to size a fallback hit/fill rect and center a label
    # for sections that don't have a real path in arena.svg.
    sections_js_data = {}
    for sec, dots in geo_sections.items():
        if not dots:
            continue
        xs = [d[0] for d in dots]
        ys = [d[1] for d in dots]
        sections_js_data[sec] = [min(xs), min(ys), max(xs), max(ys)]
    sections_json = json.dumps(sections_js_data, separators=(',', ':'))

    # Build GAMES JS array
    default_game_idx = len(games_data) - 1
    for i, g in enumerate(reversed(games_data)):
        if g["ns"] > 0:
            default_game_idx = len(games_data) - 1 - i
            break

    games_js_parts = []
    for i, g in enumerate(games_data):
        disp_ns   = g["ns"] if g["ns"] > 0 else g["pre"]
        rate_disp = g["rate"] if g["ns"] > 0 else 1.0
        dead_disp = g["dead"] if g["ns"] > 0 else g["pre"] * 180
        phantom_disp = g["phantom"] if g["ns"] > 0 else 0
        comma = "," if i < len(games_data) - 1 else ""
        games_js_parts.append(
            f'  {{label:{json.dumps(g["label"])},pre:{g["pre"]},ns:{g["ns"]},'
            f'displayNs:{disp_ns},rate:{rate_disp:.4f},dead:{dead_disp:.0f},phantom:{phantom_disp:.0f},'
            f'secPre:{json.dumps(g["sec_pre"])},secNs:{json.dumps(g["sec_ns"])}}}{comma}'
        )
    games_js = "const GAMES = [\n" + "\n".join(games_js_parts) + "\n];\nlet currentGame = " + str(default_game_idx) + ";"

    # Dropdown options
    dropdown_opts = "\n".join(
        f'        <option{"  selected" if i == default_game_idx else ""}>{g["label"]}</option>'
        for i, g in enumerate(games_data)
    )

    # Section fill polygons — real per-section geometry from arena.svg,
    # flat-filled (color set client-side per selected game via renderSections()).
    overlay_paths = []
    for sec, d_attr in sec_paths.items():
        overlay_paths.append(
            f'<path class="sec-fill" data-sec="{sec}" '
            f'd="{d_attr}" fill="#e5e4df" '
            f'transform="scale({SX:.7f},{SY:.7f})"/>'
        )
    overlay_svg = "\n          ".join(overlay_paths)

    # Fallback rects for sections that only have geometry from seatmap_geo.json
    # (no matching data-section-name path in arena.svg) — same flat-fill treatment.
    fallback_rects = []
    for sec, bbox in sections_js_data.items():
        if sec in sec_paths:
            continue
        minX, minY, maxX, maxY = bbox
        fallback_rects.append(
            f'<rect class="sec-fill" data-sec="{sec}" '
            f'x="{minX-6:.2f}" y="{minY-6:.2f}" width="{maxX-minX+12:.2f}" height="{maxY-minY+12:.2f}" '
            f'rx="3" fill="#e5e4df"/>'
        )
    fallback_svg = "\n          ".join(fallback_rects)

    featured = games_data[default_game_idx]
    title_game = featured["label"]

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{name} · Empty Seats · {title_game}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#fafaf9;--surface:#ffffff;--border:#e7e6e2;--text:#16181c;--muted:#6d7076;--muted-2:#9a9da3;
  --accent:{color};--nodata:#e5e4df;
  --f:'Inter',system-ui,-apple-system,sans-serif;
}}
html,body{{font-family:var(--f);background:var(--bg);color:var(--text);
  -webkit-font-smoothing:antialiased;margin:0;padding:0;height:100%;}}
button,select{{font-family:inherit;}}
#shell{{display:flex;flex-direction:column;height:100vh;}}

#top{{flex:none;height:56px;display:flex;align-items:center;gap:10px;
  padding:0 20px;border-bottom:1px solid var(--border);
  background:var(--surface);z-index:10;overflow:hidden;}}
@media(max-width:560px){{
  #top{{gap:6px;padding:0 12px;}}
  #brand{{display:none;}}
  .sv{{font-size:.82rem;}}
  .sl{{font-size:.44rem;}}
  .stat{{padding:0 10px;}}
  #legend{{gap:10px;}}
}}
#back-link{{font-size:.72rem;font-weight:600;color:var(--muted);
  text-decoration:none;flex:none;transition:color .15s;}}
#back-link:hover{{color:var(--text);}}
#brand{{font-size:.82rem;font-weight:700;color:var(--text);flex:none;}}
#brand span{{color:var(--muted);font-weight:500;}}
#back-btn{{display:none;font-size:.72rem;font-weight:700;color:var(--accent);
  background:var(--surface);border:1px solid var(--border);
  padding:6px 14px;border-radius:999px;cursor:pointer;transition:all .15s;flex:none;margin-left:6px;}}
#back-btn:hover{{border-color:var(--muted-2);}}
#back-btn.show{{display:block;}}
#sec-label{{font-size:.82rem;font-weight:800;color:var(--text);display:none;margin-left:8px;}}
#sec-label.show{{display:block;}}
#sec-sub{{font-size:.7rem;color:var(--muted);display:none;}}
#sec-sub.show{{display:block;}}
#game-picker{{flex:none;margin-left:auto;}}
#game-select{{font-family:var(--f);font-size:.74rem;font-weight:600;
  background:var(--surface);border:1px solid var(--border);color:var(--text);
  padding:6px 28px 6px 12px;border-radius:8px;
  cursor:pointer;outline:none;appearance:none;-webkit-appearance:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%236d7076'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 10px center;transition:border-color .15s;}}
#game-select:hover{{border-color:var(--muted-2);}}

#stage{{flex:1;display:flex;flex-direction:column;overflow:hidden;padding:16px 20px;gap:12px;min-height:0;}}
#legend{{display:flex;align-items:center;gap:18px;flex-wrap:wrap;flex:none;}}
.legend-item{{display:flex;align-items:center;gap:6px;font-size:.7rem;color:var(--muted);font-weight:500;}}
.legend-swatch{{width:10px;height:10px;border-radius:3px;flex:none;}}
.legend-scale{{display:flex;align-items:center;gap:7px;font-size:.7rem;color:var(--muted);font-weight:500;}}
.legend-scale .bar{{width:70px;height:8px;border-radius:4px;background:linear-gradient(90deg,#00e5a0,#ffd700,#ff006d);}}

#mapwrap{{flex:1;position:relative;background:var(--surface);border:1px solid var(--border);
  border-radius:16px;padding:8px;box-shadow:0 1px 2px rgba(15,20,30,.04);
  overflow:hidden;display:flex;align-items:center;justify-content:center;min-height:0;}}
#main-svg{{display:block;cursor:default;border-radius:10px;}}

#bg{{opacity:.14;filter:grayscale(.6) brightness(1.5) contrast(.7);}}
#bg.labels-hidden text{{visibility:hidden;}}

.sec-fill{{cursor:pointer;stroke:var(--surface);stroke-width:1.4;transition:opacity .12s;}}
.sec-fill:hover{{opacity:.8;}}
.sec-active{{fill:none;stroke:var(--text);stroke-width:5;pointer-events:none;}}
.sec-label-text{{font-family:var(--f);font-weight:800;pointer-events:none;
  text-anchor:middle;dominant-baseline:middle;
  paint-order:stroke;stroke:rgba(0,0,0,.18);stroke-width:.6px;}}

#stats{{flex:none;display:flex;align-items:stretch;justify-content:center;
  height:58px;border-top:1px solid var(--border);background:var(--surface);}}
.stat{{display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:0 26px;border-right:1px solid var(--border);gap:2px;}}
.stat:last-child{{border-right:none;}}
.sv{{font-size:1rem;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums;color:var(--text);}}
.sl{{font-size:.5rem;font-weight:600;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);}}
.rd{{color:var(--accent);}}

#tooltip{{position:fixed;pointer-events:none;
  background:var(--surface);border:1px solid var(--border);
  border-radius:8px;padding:9px 13px;font-size:.68rem;
  display:none;z-index:30;max-width:210px;box-shadow:0 8px 24px rgba(15,20,30,.12);}}
#tooltip.show{{display:block;}}
#tt-sec{{font-weight:800;font-size:.8rem;margin-bottom:4px;color:var(--text);}}
#tt-body{{color:var(--muted);line-height:1.6;}}
</style>
</head>
<body>
<div id="shell">
  <div id="top">
    <a id="back-link" href="nba.html">← Teams</a>
    <span id="brand">{name} <span>· {arena}</span></span>
    <button id="back-btn" onclick="resetView()">← All sections</button>
    <span id="sec-label"></span>
    <span id="sec-sub"></span>
    <div id="game-picker">
      <select id="game-select" onchange="switchGame(this.selectedIndex)">
{dropdown_opts}
      </select>
    </div>
  </div>

  <div id="stage">
    <div id="legend">
      <div class="legend-item"><div class="legend-swatch" style="background:var(--nodata)"></div>No listing data</div>
      <div class="legend-scale">Low no-show<div class="bar"></div>High no-show</div>
    </div>

    <div id="mapwrap">
      <svg id="main-svg" viewBox="0 0 {SVG_W} {SVG_H}"
           xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet"
           overflow="hidden">
        <defs>
          <clipPath id="arena-clip">
            <rect x="0" y="0" width="{SVG_W}" height="{SVG_H}"/>
          </clipPath>
        </defs>
        <g clip-path="url(#arena-clip)">
          <g id="bg" transform="scale({SX:.7f},{SY:.7f})">
            {bg_inner}
          </g>
        </g>
        <g id="court" transform="scale({SX:.7f},{SY:.7f})">
          {court_img}
        </g>
        <g id="sections">
          {overlay_svg}
          {fallback_svg}
        </g>
        <g id="sec-labels"></g>
        <g id="hover-ring"></g>
      </svg>
    </div>
  </div>

  <div id="stats">
    <div class="stat"><span class="sv" id="sv-pre">—</span><span class="sl">Secondary mkt seats</span></div>
    <div class="stat"><span class="sv rd" id="sv-ns">—</span><span class="sl">Empty at halftime</span></div>
    <div class="stat"><span class="sv rd" id="sv-rt">—</span><span class="sl">No-show rate</span></div>
    <div class="stat"><span class="sv rd" id="sv-dv">—</span><span class="sl">Dead inventory</span></div>
    <div class="stat"><span class="sv rd" id="sv-ph">—</span><span class="sl">Phantom Revenue</span></div>
  </div>
</div>

<div id="tooltip">
  <div id="tt-sec"></div>
  <div id="tt-body"></div>
</div>

<script>
const SEC_BBOX  = {sections_json};
const SEC_PATHS = {sec_paths_js};
const SVG_W = {SVG_W}, SVG_H = {SVG_H};
const SCALE_X = {SX:.7f};
const SCALE_Y = {SY:.7f};
const TEAM_COLOR = '{color}';
{games_js}

const svg      = document.getElementById('main-svg');
const NS       = document.createElementNS.bind(document, 'http://www.w3.org/2000/svg');
const tooltip  = document.getElementById('tooltip');
let zoomedSec  = null;

let vb = {{x:0, y:0, w:SVG_W, h:SVG_H}};
function applyVB() {{ svg.setAttribute('viewBox', `${{vb.x}} ${{vb.y}} ${{vb.w}} ${{vb.h}}`); }}

// Green -> amber -> red gradient by no-show rate 0..1 (mirrors Python _rate_color()).
function rateColor(r) {{
  r = Math.max(0, Math.min(1, r));
  const hx = v => Math.round(Math.max(0, Math.min(255, v))).toString(16).padStart(2, '0');
  if (r <= 0.5) {{
    const t = r * 2;
    return '#' + hx(t * 255) + hx(229 - t * 14) + hx(160 - t * 160);
  }}
  const t = (r - 0.5) * 2;
  return '#ff' + hx(215 - t * 138) + hx(t * 109);
}}

// Hide any white text baked into the arena background art (labels meant for a dark bg).
const bgGroup = document.getElementById('bg');
bgGroup.querySelectorAll('text').forEach(t => {{
  const fill = (t.getAttribute('fill') || '').toLowerCase().trim();
  if (fill === 'rgb(255,255,255)' || fill === '#ffffff' || fill === 'white' ||
      fill === '#fff' || fill === 'rgb(255, 255, 255)') {{
    t.style.display = 'none';
  }}
}});
bgGroup.classList.add('labels-hidden');

// One flat-filled shape per section (real arena.svg geometry where available,
// a bbox rect fallback otherwise), each with a centered bold label + hover/click.
const secLabelsGroup = document.getElementById('sec-labels');
const labelEls = {{}};
document.querySelectorAll('.sec-fill').forEach(el => {{
  const sec = el.dataset.sec;
  let cx, cy, w, h;
  if (el.tagName === 'path') {{
    const bb = el.getBBox();
    cx = (bb.x + bb.width / 2) * SCALE_X;
    cy = (bb.y + bb.height / 2) * SCALE_Y;
    w = bb.width * SCALE_X; h = bb.height * SCALE_Y;
  }} else {{
    cx = +el.getAttribute('x') + (+el.getAttribute('width')) / 2;
    cy = +el.getAttribute('y') + (+el.getAttribute('height')) / 2;
    w = +el.getAttribute('width'); h = +el.getAttribute('height');
  }}
  const t = NS('text');
  t.setAttribute('x', cx); t.setAttribute('y', cy);
  t.setAttribute('class', 'sec-label-text');
  t.setAttribute('font-size', Math.max(4.5, Math.min(9, Math.min(w, h) * 0.34)).toFixed(1));
  t.textContent = sec;
  secLabelsGroup.appendChild(t);
  labelEls[sec] = t;

  el.addEventListener('mouseenter', e => showTooltip(sec, e));
  el.addEventListener('mousemove',  e => moveTooltip(e));
  el.addEventListener('mouseleave', () => tooltip.classList.remove('show'));
  el.addEventListener('click', () => zoomToSection(sec));
}});

function renderSections(idx) {{
  const cg = GAMES[idx];
  document.querySelectorAll('.sec-fill').forEach(el => {{
    const sec = el.dataset.sec;
    const pre = cg.secPre[sec] || 0;
    const ns  = cg.secNs[sec]  || 0;
    const t   = labelEls[sec];
    if (pre > 0) {{
      const rate = ns / pre;
      el.setAttribute('fill', rateColor(rate));
      if (t) t.setAttribute('fill', '#ffffff');
    }} else {{
      el.setAttribute('fill', '#e5e4df');
      if (t) t.setAttribute('fill', '#6d7076');
    }}
  }});
}}

function showTooltip(sec, e) {{
  const cg   = GAMES[currentGame];
  const sPre = cg.secPre[sec] || 0;
  const sNs  = cg.secNs[sec]  || 0;
  document.getElementById('tt-sec').textContent = 'Section ' + sec;
  document.getElementById('tt-body').innerHTML = sNs > 0
    ? `<span style="color:${{TEAM_COLOR}};font-weight:700">${{sNs}} empty</span> of ${{sPre}} tracked · ${{(sNs/sPre*100).toFixed(0)}}% no-show`
    : (sPre > 0 ? `${{sPre}} tracked seats · no no-shows recorded` : 'No listing data for this section');
  tooltip.classList.add('show');
  moveTooltip(e);
}}
function moveTooltip(e) {{
  tooltip.style.left = (e.clientX + 16) + 'px';
  tooltip.style.top  = Math.min(e.clientY - 10, window.innerHeight - 110) + 'px';
}}

function updateStats() {{
  const cg = GAMES[currentGame];
  document.getElementById('sv-pre').textContent = cg.pre.toLocaleString();
  document.getElementById('sv-ns').textContent  = cg.displayNs.toLocaleString();
  document.getElementById('sv-rt').textContent  = cg.ns > 0 ? (cg.rate*100).toFixed(1)+'%' : '—';
  document.getElementById('sv-dv').textContent  = cg.dead > 0 ? '$'+Math.round(cg.dead).toLocaleString() : '—';
  document.getElementById('sv-ph').textContent  = cg.phantom > 0 ? '$'+Math.round(cg.phantom).toLocaleString() : '—';
}}

function switchGame(idx) {{
  currentGame = idx;
  document.getElementById('game-select').selectedIndex = idx;
  resetView();
  renderSections(idx);
  updateStats();
}}

function zoomToSection(sec) {{
  const bb = SEC_BBOX[sec];
  if (!bb) return;
  tooltip.classList.remove('show');
  zoomedSec = sec;
  const [minX, minY, maxX, maxY] = bb;
  const padX = Math.max((maxX-minX)*0.45, 18), padY = Math.max((maxY-minY)*0.45, 18);
  let w = (maxX-minX)+padX*2, h = (maxY-minY)+padY*2;
  const ar = SVG_W/SVG_H;
  if (w/h > ar) h = w/ar; else w = h*ar;
  const tx = (minX+maxX)/2-w/2, ty = (minY+maxY)/2-h/2;
  animVB(vb, {{x:tx,y:ty,w,h}}, 380);
  const ring = document.getElementById('hover-ring');
  ring.innerHTML = '';
  if (SEC_PATHS[sec]) {{
    const p = NS('path');
    p.setAttribute('d', SEC_PATHS[sec]);
    p.setAttribute('class', 'sec-active');
    p.setAttribute('transform', `scale(${{SCALE_X}},${{SCALE_Y}})`);
    ring.appendChild(p);
  }}
  const cg   = GAMES[currentGame];
  const sPre = cg.secPre[sec] || 0;
  const sNs  = cg.secNs[sec]  || 0;
  document.getElementById('back-btn').classList.add('show');
  document.getElementById('sec-label').textContent = 'Section ' + sec;
  document.getElementById('sec-label').classList.add('show');
  document.getElementById('sec-sub').textContent = sNs > 0
    ? `${{sNs}} empty · ${{sPre}} tracked · ${{sPre ? (sNs/sPre*100).toFixed(0) : 0}}% no-show`
    : `${{sPre}} tracked seats`;
  document.getElementById('sec-sub').classList.add('show');
  document.getElementById('sv-pre').textContent = sPre.toLocaleString();
  document.getElementById('sv-ns').textContent  = sNs.toLocaleString();
  document.getElementById('sv-rt').textContent  = sPre ? (sNs/sPre*100).toFixed(0)+'%' : '—';
  document.getElementById('sv-dv').textContent  = '—';
  document.getElementById('sv-ph').textContent  = sNs > 0 ? '$'+(sNs*35).toLocaleString()+' est.' : '—';
}}

function resetView() {{
  zoomedSec = null;
  animVB(vb, {{x:0,y:0,w:SVG_W,h:SVG_H}}, 360);
  document.getElementById('hover-ring').innerHTML = '';
  document.getElementById('back-btn').classList.remove('show');
  document.getElementById('sec-label').classList.remove('show');
  document.getElementById('sec-sub').classList.remove('show');
  updateStats();
}}

svg.addEventListener('click', e => {{ if (zoomedSec && e.target===svg) resetView(); }});

let animRaf = null;
function animVB(from, to, dur) {{
  if (animRaf) cancelAnimationFrame(animRaf);
  const f = {{...from}};
  const t0 = Date.now();
  const step = () => {{
    const p = Math.min((Date.now()-t0)/dur, 1);
    const e = p<.5 ? 2*p*p : -1+(4-2*p)*p;
    vb.x = f.x+(to.x-f.x)*e; vb.y = f.y+(to.y-f.y)*e;
    vb.w = f.w+(to.w-f.w)*e; vb.h = f.h+(to.h-f.h)*e;
    applyVB();
    if (p < 1) animRaf = requestAnimationFrame(step);
  }};
  animRaf = requestAnimationFrame(step);
}}

function clientToSVG(cx, cy) {{
  const rect = svg.getBoundingClientRect();
  return {{ x: vb.x+(cx-rect.left)/rect.width*vb.w, y: vb.y+(cy-rect.top)/rect.height*vb.h }};
}}

svg.addEventListener('wheel', e => {{
  if (!e.ctrlKey) return;
  e.preventDefault();
  if (animRaf) {{ cancelAnimationFrame(animRaf); animRaf = null; }}
  const origin = clientToSVG(e.clientX, e.clientY);
  const factor = 1 + e.deltaY * 0.008;
  const newW   = Math.min(Math.max(vb.w * factor, 30), SVG_W);
  const newH   = newW * (SVG_H / SVG_W);
  vb.x = origin.x - (origin.x - vb.x) * (newW / vb.w);
  vb.y = origin.y - (origin.y - vb.y) * (newH / vb.h);
  vb.w = newW; vb.h = newH;
  vb.x = Math.max(0, Math.min(vb.x, SVG_W - vb.w));
  vb.y = Math.max(0, Math.min(vb.y, SVG_H - vb.h));
  applyVB();
}}, {{passive: false}});

let drag = null;
svg.addEventListener('mousedown', e => {{
  if (e.button !== 0) return;
  drag = {{ sx: e.clientX, sy: e.clientY, vb: {{...vb}} }};
}});
window.addEventListener('mousemove', e => {{
  if (!drag) return;
  const rect = svg.getBoundingClientRect();
  vb.x = drag.vb.x - (e.clientX - drag.sx) / rect.width  * vb.w;
  vb.y = drag.vb.y - (e.clientY - drag.sy) / rect.height * vb.h;
  applyVB();
}});
window.addEventListener('mouseup', () => drag = null);

let touch = null, pinchDist0 = null, pinchVB0 = null, pinchMid0 = null;
let lastTap = 0, tapSec = null;
function touchDist(t) {{
  const dx=t[0].clientX-t[1].clientX, dy=t[0].clientY-t[1].clientY;
  return Math.sqrt(dx*dx+dy*dy);
}}
function touchMid(t) {{ return {{x:(t[0].clientX+t[1].clientX)/2,y:(t[0].clientY+t[1].clientY)/2}}; }}

svg.addEventListener('touchstart', e => {{
  const isZoomed = vb.w < SVG_W * 0.99;
  if (e.touches.length === 2 || isZoomed) e.preventDefault();
  tooltip.classList.remove('show');
  if (e.touches.length === 1) {{
    touch = {{ sx: e.touches[0].clientX, sy: e.touches[0].clientY, vb: {{...vb}}, moved: false }};
    pinchDist0 = null;
  }} else if (e.touches.length === 2) {{
    touch = null;
    pinchDist0 = touchDist(e.touches);
    pinchVB0   = {{...vb}};
    pinchMid0  = touchMid(e.touches);
  }}
}}, {{passive: false}});

svg.addEventListener('touchmove', e => {{
  const isZoomed = vb.w < SVG_W * 0.99;
  if (e.touches.length === 2 || isZoomed) e.preventDefault();
  if (e.touches.length === 1 && touch) {{
    const dx = e.touches[0].clientX - touch.sx;
    const dy = e.touches[0].clientY - touch.sy;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) touch.moved = true;
    if (isZoomed) {{
      const rect = svg.getBoundingClientRect();
      vb.x = Math.max(0, Math.min(touch.vb.x - dx/rect.width*vb.w, SVG_W - vb.w));
      vb.y = Math.max(0, Math.min(touch.vb.y - dy/rect.height*vb.h, SVG_H - vb.h));
      applyVB();
    }}
  }} else if (e.touches.length === 2 && pinchDist0) {{
    const dist  = touchDist(e.touches);
    const scale = pinchDist0 / dist;
    const newW  = Math.min(Math.max(pinchVB0.w * scale, 60), SVG_W);
    const newH  = newW * (SVG_H / SVG_W);
    const mid   = clientToSVG(pinchMid0.x, pinchMid0.y);
    vb.x = Math.max(0, Math.min(mid.x-(mid.x-pinchVB0.x)*(newW/pinchVB0.w), SVG_W-newW));
    vb.y = Math.max(0, Math.min(mid.y-(mid.y-pinchVB0.y)*(newH/pinchVB0.h), SVG_H-newH));
    vb.w = newW; vb.h = newH;
    applyVB();
  }}
}}, {{passive: false}});

svg.addEventListener('touchend', e => {{
  if (e.touches.length === 0 && touch && !touch.moved) {{
    const el = document.elementFromPoint(touch.sx, touch.sy);
    const hit = el && el.closest('[data-sec]');
    const now = Date.now();
    if (hit) {{
      const sec = hit.dataset.sec;
      if (now - lastTap < 350 && tapSec === sec) {{
        zoomToSection(sec);
      }} else {{
        showTooltip(sec, {{ clientX: touch.sx, clientY: touch.sy }});
        setTimeout(() => tooltip.classList.remove('show'), 2500);
      }}
      lastTap = now; tapSec = sec;
    }} else if (zoomedSec) {{
      resetView();
    }}
  }}
  touch = null; pinchDist0 = null;
}}, {{passive: false}});

function fitSvg() {{
  const wrap = document.getElementById('mapwrap');
  const ww = wrap.clientWidth, wh = wrap.clientHeight;
  const ar = SVG_W / SVG_H;
  const w = ww/wh > ar ? wh*ar : ww;
  svg.style.width = w + 'px';
  svg.style.height = (w/ar) + 'px';
}}

// Init
renderSections(currentGame);
updateStats();
fitSvg();
window.addEventListener('resize', fitSvg);
</script>
</body>
</html>"""


# ── Main ───────────────────────────────────────────────────────────────────────

def generate(team_slug):
    team_dir = f"data/{team_slug}"
    dom_svg  = f"{team_dir}/arena.svg"
    bg_svg   = f"{team_dir}/arena_full.svg"
    geo_path = f"{team_dir}/seatmap_geo.json"

    if not os.path.isfile(geo_path):
        print(f"  ✗ {team_slug}: missing seatmap_geo.json")
        return None

    game_folders = find_all_games(team_slug)
    if not game_folders:
        print(f"  ✗ {team_slug}: no game data found")
        return None

    print(f"  {team_slug}: {len(game_folders)} game(s)")
    games_data = build_games_data(team_slug, game_folders)

    # Build union of all pre_keys for geometry loading
    all_pre_keys  = set()
    all_pre_price = {}
    for g in games_data:
        all_pre_keys.update(g["pre_keys"])
        all_pre_price.update(g["pre_price"])

    sec_paths    = load_section_paths(dom_svg)
    geo_sections = load_geo_multi(geo_path, all_pre_keys, all_pre_price)
    bg_inner, court_img = load_bg_parts(bg_svg, fallback_svg=dom_svg)

    total_dots = sum(len(v) for v in geo_sections.values())
    print(f"    {len(geo_sections)} sections · {total_dots:,} dots")
    for g in games_data:
        print(f"    {g['folder']}: pre={g['pre']} ns={g['ns']}")

    html = gen_html(team_slug, games_data, sec_paths, geo_sections, bg_inner, court_img)

    os.makedirs("../docs", exist_ok=True)
    out_path = f"../docs/{team_slug}_seat_story.html"
    with open(out_path, "w") as f:
        f.write(html)
    print(f"    → {out_path}")
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python generate_team_story.py <team_slug>")
        print("       python generate_team_story.py all")
        sys.exit(1)

    if sys.argv[1] == "all":
        teams = [t for t in sorted(TEAM_META.keys())
                 if os.path.isfile(f"data/{t}/seatmap_geo.json")]
        print(f"Regenerating {len(teams)} teams...")
        ok, fail = 0, 0
        for t in teams:
            result = generate(t)
            if result: ok += 1
            else:      fail += 1
        print(f"\nDone: {ok} generated, {fail} skipped")
    else:
        team_slug = sys.argv[1]
        out = generate(team_slug)
        if out:
            os.system(f"open {out}")
