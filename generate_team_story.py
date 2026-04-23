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

        # Top sections for bar chart (top 7 by ns, fallback to pre)
        sec_counts = dict(sec_ns) if total_ns > 0 else dict(sec_pre)
        top_secs   = sorted(sec_counts.items(), key=lambda x: x[1], reverse=True)[:7]

        # Story text fields
        total_listed_value = sum(pre_price.get(k, 0) for k in pre_keys)
        half_value = round(total_listed_value / 2)
        half_str = f" Even at <strong>half their listed price</strong>, that's <strong>${half_value:,}</strong> in potential revenue from a single game." if half_value > 0 else ""
        if total_ns > 0:
            avg_price = round(dead / total_ns) if total_ns else 0
            story_headline = f"{total_ns:,} seats went empty at halftime."
            story_headline_span = str(total_ns)
            story_sub = (f"Every glowing dot is a seat listed on the secondary market before tip-off "
                         f"— and gone by halftime. At an average of "
                         f"${avg_price:,}/seat, that's ${dead:,.0f} in dead inventory in a single game."
                         f"{half_str}")
            chart_note = "Every listed seat in these sections was empty by halftime."
            chart_title_prefix = "Top sections by no-shows"
        else:
            story_headline = f"{total_pre:,} seats listed on the secondary market."
            story_headline_span = str(total_pre)
            story_sub = (f"These are all seats listed on Ticketmaster's secondary market before tip-off "
                         f"for this game — real inventory that fans paid for but may not show up to use."
                         f"{half_str}")
            chart_note = "Top sections by secondary market listings pre-game."
            chart_title_prefix = "Top sections pre-game"
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


# ── Story HTML builder ─────────────────────────────────────────────────────────

def gen_story_html(team_slug, games_data, color):
    """Generate the scroll story section — headline/chart update dynamically via JS."""
    name  = TEAM_META.get(team_slug, {}).get("name", team_slug.title())
    arena = TEAM_META.get(team_slug, {}).get("arena", "")

    # Build JS story data array — one entry per game
    story_js_rows = []
    for g in games_data:
        top = g["topSecs"]
        max_count = top[0][1] if top else 1
        secs_js = "[" + ",".join(
            f'{{sec:{json.dumps(sec)},count:{count},pct:{round(count/max_count*100)}}}'
            for sec, count in top
        ) + "]"
        # Escape single quotes in sub text
        sub_escaped = g["sub"].replace("'", "\\'").replace("$", "\\$")
        story_js_rows.append(
            f'  {{headlineSpan:{json.dumps(g["headlineSpan"])},headlineRest:{json.dumps(g["headlineRest"])},'
            f'sub:{json.dumps(g["sub"])},chartNote:{json.dumps(g["chartNote"])},'
            f'chartTitle:{json.dumps(g["chartTitlePrefix"] + " · " + g["label"])},'
            f'kicker:{json.dumps(arena + " · " + g["story"])},'
            f'topSecs:{secs_js}}}'
        )
    story_js = "const STORY_DATA = [\n" + ",\n".join(story_js_rows) + "\n];"

    # Comparison table rows (static — shows all games always)
    table_rows = ""
    for g in games_data:
        opp_display = g["opp"].split("(")[0].strip() if g["opp"] else g["folder"].replace("_", " ").title()
        if g["ns"] > 0:
            ns_cell   = f'<td class="hi">{g["ns"]:,} ({g["rate"]:.0%})</td>'
            dead_cell = f'<td class="hi">${g["dead"]:,.0f}</td>'
            row_style = ' style="background:rgba(255,255,255,.03)"'
            chip = '<span class="dot-chip"></span>'
        else:
            ns_cell   = '<td style="color:rgba(200,185,185,.3)">—</td>'
            dead_cell = '<td style="color:rgba(200,185,185,.3)">—</td>'
            row_style = ""
            chip = ""
        try:
            d = datetime.strptime(g["date"], "%Y-%m-%d")
            date_disp = d.strftime("%b %-d")
        except Exception:
            date_disp = g["date"]
        table_rows += f'      <tr{row_style}>\n        <td>{date_disp}</td><td>{chip}{opp_display}</td><td>{g["pre"]:,}</td>{ns_cell}{dead_cell}\n      </tr>\n'

    return f"""
<!-- ── Story Section ─────────────────────────────────────────────────────── -->
<div id="story-wrap">
<div id="story">
<div id="story-inner">

  <div class="story-kicker" id="story-kicker"></div>
  <div class="story-headline" id="story-headline"></div>
  <p class="story-sub" id="story-sub"></p>

  <div class="story-divider"></div>

  <div class="chart-title" id="chart-title"></div>
  <div id="bar-chart"></div>
  <p id="chart-note" style="font-size:.58rem;color:rgba(200,185,185,.3);margin-top:14px;"></p>

  <div class="story-divider"></div>

  <div id="table-reveal" style="transform:translateY(30px);opacity:0;transition:transform .8s cubic-bezier(.22,1,.36,1),opacity .8s ease;">
  <div class="chart-title">All tracked {name} games</div>
  <table class="comp-table">
    <thead>
      <tr><th>Game</th><th>Opponent</th><th>Pre-game seats</th><th>Empty at halftime</th><th>Est. dead $</th></tr>
    </thead>
    <tbody>
{table_rows}    </tbody>
  </table>
  <p style="font-size:.58rem;color:rgba(200,185,185,.3);margin-top:12px;">More games added as the playoffs continue.</p>
  </div>

</div><!-- #story-inner -->
</div><!-- #story -->
</div><!-- #story-wrap -->
<script>
{story_js}
</script>"""


# ── Main HTML generator ────────────────────────────────────────────────────────

def gen_html(team_slug, games_data, sec_paths, geo_sections, bg_inner, court_img):
    meta  = TEAM_META.get(team_slug, {"name": team_slug.title(), "arena": "", "color": "#ce1141"})
    color = meta["color"]
    name  = meta["name"]
    arena = meta["arena"]

    if meta.get("bold_arena"):
        dk_filter = '''<filter id="dk">
          <feColorMatrix type="saturate" values="0.22"/>
          <feColorMatrix type="matrix"
            values="0.52 0 0 0 0.03
                    0    0.44 0 0 0.03
                    0    0 0.48 0 0.06
                    0    0 0    1 0"/>
        </filter>'''
    else:
        dk_filter = '''<filter id="dk">
          <feColorMatrix type="matrix"
            values="0.32 0 0 0 0.02
                    0    0.23 0 0 0.02
                    0    0 0.27 0 0.05
                    0    0 0    1 0"/>
        </filter>'''

    sec_paths_js = json.dumps(sec_paths, separators=(',', ':'))

    # Pack geo_sections (no per-game status — just coordinates + row/seat for tracked dots)
    sections_js_data = {}
    for sec, dots in geo_sections.items():
        packed = []
        for d in dots:
            if d[2] == 1:
                packed.append([d[0], d[1], 1, d[3], d[4], d[5]])
            else:
                packed.append([d[0], d[1], 0])
        sections_js_data[sec] = {"dots": packed}
    sections_json = json.dumps(sections_js_data, separators=(',', ':'))

    # Build GAMES JS array
    default_game_idx = len(games_data) - 1
    for i, g in enumerate(reversed(games_data)):
        if g["ns"] > 0:
            default_game_idx = len(games_data) - 1 - i
            break

    games_js_parts = []
    for i, g in enumerate(games_data):
        ns_list  = [f"{s}_{r}_{seat}" for s, r, seat in g["ns_keys"]]
        pre_list = [f"{s}_{r}_{seat}" for s, r, seat in g["pre_keys"]]
        disp_ns  = g["ns"] if g["ns"] > 0 else g["pre"]
        rate_disp = g["rate"] if g["ns"] > 0 else 1.0
        dead_disp = g["dead"] if g["ns"] > 0 else g["pre"] * 180
        comma = "," if i < len(games_data) - 1 else ""
        games_js_parts.append(
            f'  {{label:{json.dumps(g["label"])},pre:{g["pre"]},ns:{g["ns"]},'
            f'displayNs:{disp_ns},rate:{rate_disp:.4f},dead:{dead_disp:.0f},'
            f'nsKeys:new Set({json.dumps(ns_list)}),'
            f'preKeys:new Set({json.dumps(pre_list)}),'
            f'secPre:{json.dumps(g["sec_pre"])},secNs:{json.dumps(g["sec_ns"])}}}{comma}'
        )
    games_js = "const GAMES = [\n" + "\n".join(games_js_parts) + "\n];\nlet currentGame = " + str(default_game_idx) + ";"

    # Dropdown options
    dropdown_opts = "\n".join(
        f'        <option{"  selected" if i == default_game_idx else ""}>{g["label"]}</option>'
        for i, g in enumerate(games_data)
    )

    # Section hit area paths
    overlay_paths = []
    for sec, d_attr in sec_paths.items():
        overlay_paths.append(
            f'<path class="sec-hit" data-sec="{sec}" '
            f'd="{d_attr}" fill="transparent" stroke="rgba(255,255,255,0)" stroke-width="0" '
            f'style="cursor:pointer" transform="scale({SX:.7f},{SY:.7f})"/>'
        )
    overlay_svg = "\n          ".join(overlay_paths)

    story_html = gen_story_html(team_slug, games_data, color)

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
  --bg:#05070d;--f:'Inter',sans-serif;
  --red:{color};--dim:rgba(200,185,185,.4);--border:rgba(255,255,255,.07);
  --blue:#4a90d9;--gray:rgba(180,180,180,.18);
}}
html,body{{font-family:var(--f);background:var(--bg);color:#f0e8e8;
  -webkit-font-smoothing:antialiased;margin:0;padding:0;}}
#shell{{display:flex;flex-direction:column;height:100vh;scroll-snap-align:start;scroll-snap-stop:always;}}

#top{{flex:none;height:48px;display:flex;align-items:center;gap:10px;
  padding:0 12px;border-bottom:1px solid var(--border);
  background:rgba(5,7,13,.98);z-index:10;overflow:hidden;}}
@media(max-width:500px){{
  #top{{gap:6px;padding:0 8px;}}
  #brand{{display:none;}}
  .sv{{font-size:.75rem;}}
  .sl{{font-size:.42rem;}}
  .stat{{padding:0 10px;}}
  #legend{{bottom:62px;left:8px;}}
  .ll{{font-size:.5rem;}}
}}
#back-link{{font-size:.6rem;font-weight:600;letter-spacing:.12em;text-transform:uppercase;
  color:rgba(200,185,185,.35);text-decoration:none;flex:none;transition:color .18s;}}
#back-link:hover{{color:rgba(200,185,185,.8);}}
#brand{{font-size:.7rem;font-weight:800;letter-spacing:.12em;
  text-transform:uppercase;color:rgba(220,200,200,.5);flex:none;}}
#game-picker{{flex:none;margin-left:4px;}}
#game-select{{font-family:var(--f);font-size:.58rem;font-weight:600;
  background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.12);
  color:rgba(200,185,185,.8);padding:3px 24px 3px 9px;border-radius:6px;
  cursor:pointer;outline:none;appearance:none;-webkit-appearance:none;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='rgba(200,185,185,.4)'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 7px center;transition:border-color .18s;}}
#game-select:hover{{border-color:rgba(255,255,255,.3);}}
#game-select option{{background:#0d1117;color:#e0d8d8;}}
#back-btn{{display:none;font-family:var(--f);font-size:.62rem;font-weight:600;
  background:transparent;border:1px solid var(--border);color:var(--dim);
  padding:3px 12px;border-radius:999px;cursor:pointer;transition:all .18s;flex:none;}}
#back-btn:hover{{border-color:rgba(255,255,255,.3);color:#fff;}}
#back-btn.show{{display:block;}}
#sec-label{{font-size:.8rem;font-weight:700;color:#fff;display:none;}}
#sec-label.show{{display:block;}}
#sec-sub{{font-size:.6rem;color:var(--dim);display:none;}}
#sec-sub.show{{display:block;}}

#arena-wrap{{flex:1;display:flex;align-items:center;justify-content:center;
  overflow:hidden;background:#05070d;}}
#main-svg{{display:block;cursor:default;}}

#bg.labels-hidden text {{ visibility:hidden; }}

circle.gray{{ fill:rgba(160,160,180,.18); }}
circle.tracked{{ fill:{color}22; }}
circle.red{{ fill:{color}; }}
circle.red:hover{{ fill:#fff; }}

.sec-active{{fill:none;stroke:rgba(255,255,255,.55);stroke-width:6;pointer-events:none;}}
.sec-hit{{pointer-events:fill;}}

#stats{{flex:none;display:flex;align-items:stretch;justify-content:center;
  height:50px;border-top:1px solid var(--border);background:rgba(5,7,13,.98);}}
.stat{{display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:0 24px;border-right:1px solid var(--border);gap:1px;}}
.stat:last-child{{border-right:none;}}
.sv{{font-size:.9rem;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums;}}
.sl{{font-size:.48rem;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);}}
.rd{{color:{color};}}

#legend{{position:absolute;left:14px;bottom:58px;
  display:flex;flex-direction:column;gap:5px;pointer-events:none;}}
.lrow{{display:flex;align-items:center;gap:7px;}}
.ld{{width:9px;height:9px;border-radius:50%;flex:none;}}
.ll{{font-size:.54rem;font-weight:500;color:var(--dim);}}

#tooltip{{position:fixed;pointer-events:none;
  background:rgba(8,12,24,.94);border:1px solid rgba(255,255,255,.1);
  border-radius:8px;padding:9px 13px;font-size:.62rem;
  display:none;z-index:30;max-width:200px;}}
#tooltip.show{{display:block;}}
#tt-sec{{font-weight:700;font-size:.78rem;margin-bottom:4px;color:#fff;}}
#tt-body{{color:var(--dim);line-height:1.65;}}

#seat-tooltip{{position:fixed;pointer-events:none;
  background:rgba(8,12,24,.96);border:1px solid {color}59;
  border-radius:8px;padding:8px 12px;display:none;z-index:40;white-space:nowrap;}}
#seat-tooltip.show{{display:block;}}
.st-sec{{font-size:.68rem;font-weight:700;color:#fff;margin-bottom:3px;}}
.st-tag{{font-size:.58rem;color:{color};font-weight:600;margin-bottom:2px;}}
.st-price{{font-size:.6rem;color:var(--dim);}}

.scroll-hint{{position:absolute;bottom:58px;right:16px;
  display:flex;flex-direction:column;align-items:center;gap:3px;
  animation:bounceHint 2s ease-in-out infinite;pointer-events:none;opacity:.4;}}
.scroll-hint span{{font-size:.44rem;letter-spacing:.12em;text-transform:uppercase;color:rgba(200,185,185,.6);}}
@keyframes bounceHint{{0%,100%{{transform:translateY(0)}}50%{{transform:translateY(5px)}}}}

/* ── Story section ───────────────────────────────────────────────────── */
#story-wrap{{height:100vh;overflow-y:auto;scroll-snap-align:start;scroll-snap-stop:always;flex-shrink:0;}}
#story{{background:var(--bg);padding:56px 24px 80px;max-width:860px;margin:0 auto;}}
#story-inner{{transform:translateY(40px);opacity:0;transition:transform .7s cubic-bezier(.22,1,.36,1),opacity .7s ease;}}
#story-inner.revealed{{transform:translateY(0);opacity:1;}}
.story-kicker{{font-size:.6rem;font-weight:700;letter-spacing:.15em;text-transform:uppercase;color:{color};margin-bottom:10px;}}
.story-headline{{font-size:2rem;font-weight:800;line-height:1.15;color:#fff;margin-bottom:10px;}}
.story-headline span{{color:{color};}}
.story-sub{{font-size:.85rem;color:rgba(200,185,185,.55);line-height:1.7;max-width:520px;}}
.story-divider{{height:1px;background:var(--border);margin:40px 0;}}
.chart-title{{font-size:.65rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:rgba(200,185,185,.4);margin-bottom:16px;}}
.bar-row{{display:flex;align-items:center;gap:10px;margin-bottom:10px;}}
.bar-label{{font-size:.62rem;font-weight:600;color:rgba(200,185,185,.6);width:56px;text-align:right;flex:none;}}
.bar-track{{flex:1;height:6px;background:rgba(255,255,255,.05);border-radius:3px;overflow:hidden;}}
.bar-fill{{height:100%;border-radius:3px;background:{color};box-shadow:0 0 8px {color}88;transition:width 1s cubic-bezier(.22,1,.36,1);}}
.bar-val{{font-size:.6rem;color:rgba(200,185,185,.45);width:46px;flex:none;}}
.comp-table{{width:100%;border-collapse:collapse;margin-top:8px;}}
.comp-table th{{font-size:.55rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:rgba(200,185,185,.3);padding:6px 10px;text-align:left;border-bottom:1px solid var(--border);}}
.comp-table td{{font-size:.68rem;padding:10px 10px;border-bottom:1px solid rgba(255,255,255,.04);color:rgba(200,185,185,.7);}}
.comp-table tr:hover td{{background:rgba(255,255,255,.02);}}
.comp-table .hi{{color:{color};font-weight:700;}}
.dot-chip{{display:inline-block;width:7px;height:7px;border-radius:50%;
  background:{color};box-shadow:0 0 5px {color}99;margin-right:5px;vertical-align:middle;}}
@media(max-width:600px){{
  .story-headline{{font-size:1.35rem;}}
  #story{{padding:36px 16px 60px;}}
}}
/* Scroll snap container */
#page{{height:100vh;overflow-y:scroll;scroll-snap-type:y mandatory;scroll-behavior:smooth;-webkit-overflow-scrolling:touch;}}
</style>
</head>
<body>
<div id="page">
<div id="shell">
  <div id="top">
    <a id="back-link" href="nba.html">← Teams</a>
    <span id="brand">{name} · {arena}</span>
    <div id="game-picker">
      <select id="game-select" onchange="switchGame(this.selectedIndex)">
{dropdown_opts}
      </select>
    </div>
    <button id="back-btn" onclick="resetView()">← All sections</button>
    <span id="sec-label"></span>
    <span id="sec-sub"></span>
  </div>

  <div id="arena-wrap">
    <svg id="main-svg" viewBox="0 0 {SVG_W} {SVG_H}"
         xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet"
         overflow="hidden">
      <defs>
        {dk_filter}
        <filter id="glow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur in="SourceGraphic" stdDeviation="2.5" result="blur"/>
          <feMerge><feMergeNode in="blur"/><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        <clipPath id="arena-clip">
          <rect x="0" y="0" width="{SVG_W}" height="{SVG_H}"/>
        </clipPath>
      </defs>
      <g clip-path="url(#arena-clip)">
      <g id="bg" filter="url(#dk)" transform="scale({SX:.7f},{SY:.7f})">
        {bg_inner}
      </g>
      </g>
      <g id="court" xmlns:xlink="http://www.w3.org/1999/xlink" transform="scale({SX:.7f},{SY:.7f})">
        {court_img}
      </g>
      <g id="dots-bg"></g>
      <g id="sec-hits">
        {overlay_svg}
      </g>
      <g id="dots-ns" filter="url(#glow)">
        <animate attributeName="opacity" values="0.6;1;0.6" dur="1.8s" repeatCount="indefinite"/>
      </g>
      <g id="hover-ring"></g>
    </svg>
  </div>

  <div id="stats">
    <div class="stat"><span class="sv" id="sv-pre">—</span><span class="sl">Secondary mkt seats</span></div>
    <div class="stat"><span class="sv rd" id="sv-ns">—</span><span class="sl">Empty at halftime</span></div>
    <div class="stat"><span class="sv rd" id="sv-rt">—</span><span class="sl">No-show rate</span></div>
    <div class="stat"><span class="sv rd" id="sv-dv">—</span><span class="sl">Dead inventory</span></div>
  </div>
  <div class="scroll-hint">
    <span>scroll</span>
    <svg width="12" height="8" viewBox="0 0 12 8"><path d="M1 1l5 5 5-5" stroke="rgba(200,185,185,.5)" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg>
  </div>
</div>

{story_html}

</div><!-- #page -->

<div id="legend">
  <div class="lrow"><div class="ld" style="background:rgba(160,160,180,.3)"></div><span class="ll">All seats</span></div>
  <div class="lrow"><div class="ld" style="background:{color};box-shadow:0 0 5px {color}99"></div><span class="ll">Empty at halftime</span></div>
</div>

<div id="tooltip">
  <div id="tt-sec"></div>
  <div id="tt-body"></div>
</div>
<div id="seat-tooltip"></div>

<script>
const SECS      = {sections_json};
const SEC_PATHS = {sec_paths_js};
const SVG_W = {SVG_W}, SVG_H = {SVG_H};
const SCALE_X = {SX:.7f};
const SCALE_Y = {SY:.7f};
const TEAM_COLOR = '{color}';
{games_js}

const svg      = document.getElementById('main-svg');
const NS       = document.createElementNS.bind(document, 'http://www.w3.org/2000/svg');
const dotsBg   = document.getElementById('dots-bg');
const dotsNs   = document.getElementById('dots-ns');
const tooltip  = document.getElementById('tooltip');
let secDots    = {{}};
let zoomedSec  = null;

let vb = {{x:0, y:0, w:SVG_W, h:SVG_H}};
function applyVB() {{ svg.setAttribute('viewBox', `${{vb.x}} ${{vb.y}} ${{vb.w}} ${{vb.h}}`); }}

const seatTooltip = document.getElementById('seat-tooltip');

function buildDots(gameIdx) {{
  dotsBg.innerHTML = '';
  dotsNs.innerHTML = '';
  secDots = {{}};
  const g = GAMES[gameIdx];
  // If no halftime data, treat pre-game seats as the glowing dots (demo mode)
  const nsKeys  = g.ns > 0 ? g.nsKeys  : g.preKeys;
  const preKeys = g.ns > 0 ? g.preKeys : new Set();

  for (const [sec, data] of Object.entries(SECS)) {{
    const bgGroup = NS('g');
    const nsGroup = NS('g');
    const circles = [];

    for (const d of data.dots) {{
      const c = NS('circle');
      c.setAttribute('cx', d[0]);
      c.setAttribute('cy', d[1]);
      const row = d[3] || '', seat = d[4] || '';
      const key = row && seat ? `${{sec}}_${{row}}_${{seat}}` : null;
      const isNs  = key ? nsKeys.has(key)  : false;
      const isPre = key ? preKeys.has(key) : false;

      if (isNs) {{
        c.setAttribute('r', '2.2');
        c.setAttribute('class', 'red');
        c.dataset.sec   = sec;
        c.dataset.row   = row;
        c.dataset.seat  = seat;
        c.dataset.price = d[5] || 0;
        c.style.cursor  = 'crosshair';
        c.addEventListener('mouseenter', showSeatTooltip);
        c.addEventListener('mousemove',  moveSeatTooltip);
        c.addEventListener('mouseleave', hideSeatTooltip);
        nsGroup.appendChild(c);
      }} else if (isPre) {{
        c.setAttribute('r', '1.6');
        c.setAttribute('class', 'tracked');
        bgGroup.appendChild(c);
      }} else {{
        c.setAttribute('r', '1.2');
        c.setAttribute('class', 'gray');
        bgGroup.appendChild(c);
      }}
      c.style.opacity = '0';
      circles.push(c);
    }}

    dotsBg.appendChild(bgGroup);
    dotsNs.appendChild(nsGroup);
    secDots[sec] = circles;
  }}
}}

function showSeatTooltip(e) {{
  const c = e.target;
  const price = +c.dataset.price;
  seatTooltip.innerHTML =
    `<div class="st-sec">Sec ${{c.dataset.sec}} · Row ${{c.dataset.row}} · Seat ${{c.dataset.seat}}</div>` +
    `<div class="st-tag">Empty at halftime</div>` +
    (price > 0 ? `<div class="st-price">Listed at <strong>$${{price}}</strong></div>` : '');
  seatTooltip.classList.add('show');
  moveSeatTooltip(e);
}}
function moveSeatTooltip(e) {{
  const tw = seatTooltip.offsetWidth;
  const left = Math.min(e.clientX + 14, window.innerWidth - tw - 8);
  seatTooltip.style.left = left + 'px';
  seatTooltip.style.top  = Math.min(e.clientY - 10, window.innerHeight - 90) + 'px';
}}
function hideSeatTooltip() {{ seatTooltip.classList.remove('show'); }}

function runEntrance() {{
  const CX = SVG_W / 2, CY = SVG_H / 2;
  const PI2 = Math.PI * 2;
  const START_ANGLE = -Math.PI / 2;

  const allDots = [];
  for (const circles of Object.values(secDots)) {{
    for (const c of circles) {{
      const cx = +c.getAttribute('cx'), cy = +c.getAttribute('cy');
      let a = Math.atan2(cy - CY, cx - CX) - START_ANGLE;
      if (a < 0) a += PI2;
      allDots.push({{ c, a, isNs: c.classList.contains('red') }});
    }}
  }}
  allDots.sort((a, b) => a.a - b.a);

  const nonNs = allDots.filter(d => !d.isNs);
  const ns    = allDots.filter(d =>  d.isNs);
  const P1_DUR = 1200, P2_START = 1300, P2_DUR = 900;

  nonNs.forEach((d, i) => d.revealAt = (i / Math.max(nonNs.length - 1, 1)) * P1_DUR);
  ns.forEach((d, i)    => d.revealAt = P2_START + (i / Math.max(ns.length - 1, 1)) * P2_DUR);

  let p1Idx = 0, p2Idx = 0;
  const t0 = performance.now();
  function frame(now) {{
    const elapsed = now - t0;
    while (p1Idx < nonNs.length && nonNs[p1Idx].revealAt <= elapsed) {{
      nonNs[p1Idx].c.style.opacity = '1'; p1Idx++;
    }}
    while (p2Idx < ns.length && ns[p2Idx].revealAt <= elapsed) {{
      ns[p2Idx].c.style.opacity = '1'; p2Idx++;
    }}
    if (p1Idx < nonNs.length || p2Idx < ns.length) requestAnimationFrame(frame);
  }}
  requestAnimationFrame(frame);
}}

function showTooltip(sec, e) {{
  const data = SECS[sec];
  if (!data) return;
  const cg   = GAMES[currentGame];
  const sPre = cg.secPre[sec] || 0;
  const sNs  = cg.secNs[sec]  || 0;
  document.getElementById('tt-sec').textContent = 'Section ' + sec;
  document.getElementById('tt-body').innerHTML =
    (sNs > 0
      ? `<span style="color:${{TEAM_COLOR}};font-weight:600">${{sNs}} empty</span> of ${{sPre}} tracked<br>`
      : (sPre > 0 ? `${{sPre}} tracked seats<br>` : '')) +
    (sNs > 0 && cg.secNs[sec] ? `` : ``);
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
}}

function renderStory(idx) {{
  const sd = STORY_DATA[idx];
  if (!sd) return;
  document.getElementById('story-kicker').textContent    = sd.kicker;
  document.getElementById('story-headline').innerHTML    = '<span>' + sd.headlineSpan + '</span> ' + sd.headlineRest;
  document.getElementById('story-sub').innerHTML         = sd.sub;
  document.getElementById('chart-title').textContent     = sd.chartTitle;
  document.getElementById('chart-note').textContent      = sd.chartNote;
  const barChart = document.getElementById('bar-chart');
  barChart.innerHTML = sd.topSecs.map(s =>
    `<div class="bar-row"><span class="bar-label">Sec ${{s.sec}}</span><div class="bar-track"><div class="bar-fill" style="width:0%" data-w="${{s.pct}}"></div></div><span class="bar-val">${{s.count}} seats</span></div>`
  ).join('');
  // animate bars after a short delay (allow paint)
  requestAnimationFrame(() => {{
    requestAnimationFrame(() => {{
      barChart.querySelectorAll('.bar-fill').forEach(b => b.style.width = b.dataset.w + '%');
    }});
  }});
}}

function switchGame(idx) {{
  currentGame = idx;
  document.getElementById('game-select').selectedIndex = idx;
  resetView();
  buildDots(idx);
  runEntrance();
  updateStats();
  renderStory(idx);
}}

const bgGroup = document.getElementById('bg');
bgGroup.querySelectorAll('text').forEach(t => {{
  const fill = (t.getAttribute('fill') || '').toLowerCase().trim();
  if (fill === 'rgb(255,255,255)' || fill === '#ffffff' || fill === 'white' ||
      fill === '#fff' || fill === 'rgb(255, 255, 255)') {{
    t.style.display = 'none';
  }}
}});
bgGroup.classList.add('labels-hidden');

const secHitsGroup = document.getElementById('sec-hits');
const PAD = 6;
for (const [sec, data] of Object.entries(SECS)) {{
  if (!data.dots.length) continue;
  const existing = secHitsGroup.querySelector(`[data-sec="${{sec}}"]`);
  let hitEl;
  if (existing) {{
    hitEl = existing;
  }} else {{
    let minX=Infinity, minY=Infinity, maxX=-Infinity, maxY=-Infinity;
    for (const d of data.dots) {{
      if (d[0]<minX) minX=d[0]; if (d[0]>maxX) maxX=d[0];
      if (d[1]<minY) minY=d[1]; if (d[1]>maxY) maxY=d[1];
    }}
    const r = NS('rect');
    r.setAttribute('x', minX-PAD); r.setAttribute('y', minY-PAD);
    r.setAttribute('width', (maxX-minX)+PAD*2); r.setAttribute('height', (maxY-minY)+PAD*2);
    r.setAttribute('rx','3'); r.setAttribute('class','sec-hit');
    r.dataset.sec = sec; r.setAttribute('fill','transparent'); r.setAttribute('stroke','none');
    r.style.cursor = 'pointer';
    secHitsGroup.appendChild(r);
    hitEl = r;
  }}
  hitEl.addEventListener('mouseenter', e => showTooltip(sec, e));
  hitEl.addEventListener('mousemove',  e => moveTooltip(e));
  hitEl.addEventListener('mouseleave', () => tooltip.classList.remove('show'));
  hitEl.addEventListener('click', () => zoomToSection(sec));
}}

function zoomToSection(sec) {{
  const data = SECS[sec];
  if (!data || !data.dots.length) return;
  tooltip.classList.remove('show');
  zoomedSec = sec;
  const xs = data.dots.map(d=>d[0]), ys = data.dots.map(d=>d[1]);
  const minX=Math.min(...xs), maxX=Math.max(...xs);
  const minY=Math.min(...ys), maxY=Math.max(...ys);
  const padX=Math.max((maxX-minX)*0.45,18), padY=Math.max((maxY-minY)*0.45,18);
  let w=(maxX-minX)+padX*2, h=(maxY-minY)+padY*2;
  const ar=SVG_W/SVG_H;
  if (w/h>ar) h=w/ar; else w=h*ar;
  const tx=(minX+maxX)/2-w/2, ty=(minY+maxY)/2-h/2;
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
  const wrap = document.getElementById('arena-wrap');
  const ww = wrap.clientWidth, wh = wrap.clientHeight;
  const ar = SVG_W / SVG_H;
  const w = ww/wh > ar ? wh*ar : ww;
  svg.style.width = w + 'px';
  svg.style.height = (w/ar) + 'px';
}}

// Scroll story reveal
const page = document.getElementById('page');
const storyInner = document.getElementById('story-inner');
let storyRevealed = false;
page.addEventListener('scroll', () => {{
  if (!storyRevealed && page.scrollTop > window.innerHeight * 0.3) {{
    storyRevealed = true;
    storyInner.classList.add('revealed');
  }}
}}, {{passive: true}});

// Table reveal
const tableEl = document.getElementById('table-reveal');
const tableObs = new IntersectionObserver(entries => {{
  entries.forEach(e => {{
    if (e.isIntersecting) {{
      tableEl.style.transform = 'translateY(0)';
      tableEl.style.opacity   = '1';
      tableObs.disconnect();
    }}
  }});
}}, {{threshold: 0.2, root: document.getElementById('story-wrap')}});
if (tableEl) tableObs.observe(tableEl);

// Init
buildDots(currentGame);
runEntrance();
updateStats();
renderStory(currentGame);
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

    os.makedirs("docs", exist_ok=True)
    out_path = f"docs/{team_slug}_seat_story.html"
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
