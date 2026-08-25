"""
generate_seat_story.py  —  top-down arena view using real TM arena SVG + geometry

The arena background is the actual TM SVG (data/magic/arena.svg), viewBox 0 0 10240 7680.
Dot coordinates come from seatmap_geo.json which uses that same coordinate space,
so we just scale: svg_x = canvas_x / 10240 * W, svg_y = canvas_y / 7680 * H.

Lower bowl (101-118) and club sections are estimated since the G-League geo
only has upper deck + floor sections.
"""
import csv, json, math, os
from collections import defaultdict

# ── Games ─────────────────────────────────────────────────────────────────────
GAMES = [
    ("data/magic/2026-03-31_phoenix_suns_at_magic",           "vs Suns · Mar 31"),
    ("data/magic/2026-04-01_atlanta_hawks_at_magic",          "vs Hawks · Apr 1"),
    ("data/magic/2026-04-08_minnesota_timberwolves_at_magic", "vs TWolves · Apr 8"),
]

# ── SVG viewport — matches TM arena SVG viewBox aspect ratio (10240:7680 = 4:3) ──
SVG_W = 900
SVG_H = 675   # 900 * 7680/10240

# TM canvas extents (from the arena SVG viewBox)
TM_W = 10240.0
TM_H = 7680.0

def to_svg(cx, cy):
    """Canvas coords → SVG pixel coords."""
    return round(cx / TM_W * SVG_W, 1), round(cy / TM_H * SVG_H, 1)

# Arena centre (canvas coords, derived from COURTSIDE geometry)
ARENA_CX, ARENA_CY = 5132, 3800   # approx centre of the TM canvas
ARENA_SX, ARENA_SY = to_svg(ARENA_CX, ARENA_CY)


# ── Section centroid map ───────────────────────────────────────────────────────

def build_section_map():
    with open("data/magic/seatmap_geo.json") as f:
        geo = json.load(f)
    page = geo.get("pages", [geo])[0]

    exact_canvas = {}

    def extract(seg):
        name = seg.get("name", "")
        cat  = seg.get("segmentCategory", "")
        places = seg.get("placesNoKeys", [])
        xs, ys = [], []
        for p in places:
            if len(p) >= 4:
                xs.append(p[2]); ys.append(p[3])
        for child in seg.get("segments", []):
            cx2, cy2, n = extract(child)
            xs += [cx2] * n; ys += [cy2] * n
        if cat == "COMPOSITE" and xs:
            exact_canvas[name] = (sum(xs) / len(xs), sum(ys) / len(ys))
        return (sum(xs)/len(xs) if xs else 0,
                sum(ys)/len(ys) if ys else 0, len(xs))

    for seg in page.get("segments", []):
        extract(seg)

    smap = {}

    # ── Upper deck 201-232: exact geometry ────────────────────────────────────
    for name, (cx, cy) in exact_canvas.items():
        if name.isdigit() and 200 <= int(name) <= 232:
            smap[name] = to_svg(cx, cy)

    # ── Floor/courtside: exact geometry ───────────────────────────────────────
    for name, (cx, cy) in exact_canvas.items():
        if any(name.startswith(p) for p in
               ("COURTSIDE", "FLOORSIDE", "BASELINE", "HW")):
            smap[name] = to_svg(cx, cy)

    # ── Floor sideline groupings ───────────────────────────────────────────────
    def avg_canvas(names):
        pts = [exact_canvas[n] for n in names if n in exact_canvas]
        if not pts: return (ARENA_CX, ARENA_CY)
        return sum(x for x,y in pts)/len(pts), sum(y for x,y in pts)/len(pts)

    # From visual inspection of geo data:
    # FLOORSIDE 1-10  = north sideline (top of canvas, low canvas_y)
    # FLOORSIDE 15-24 = south sideline (bottom of canvas, high canvas_y)
    # FLOORSIDE 11-14 = east baseline  (right, high canvas_x)
    # FLOORSIDE 25-28 = west baseline  (left, low canvas_x)
    smap["FLOORN"] = to_svg(*avg_canvas([f"FLOORSIDE {i}" for i in range(1,  11)]))
    smap["FLOORS"] = to_svg(*avg_canvas([f"FLOORSIDE {i}" for i in range(15, 25)]))
    smap["FLOORE"] = to_svg(*avg_canvas([f"FLOORSIDE {i}" for i in range(11, 15)]))
    smap["FLOORW"] = to_svg(*avg_canvas([f"FLOORSIDE {i}" for i in range(25, 29)]))

    # ── Legends Suites A/B (from visual map position) ─────────────────────────
    smap["LGNDA"] = to_svg(7400, 2800)
    smap["LGNDB"] = to_svg(7400, 4800)

    # ── LC / LE courtside floor ────────────────────────────────────────────────
    smap["LC"] = to_svg(3800, 3400)
    smap["LE"] = to_svg(3800, 4200)

    # ── Lower bowl 101-118: estimated inner ellipse ────────────────────────────
    # Upper deck spans roughly canvas x: 1200-8900, y: 850-6850
    # Arena centre canvas: ~5000, 3800
    # Lower bowl at ~55% radius
    cx0, cy0 = 5000.0, 3800.0
    # rx/ry from upper deck span
    rx_up = (8900 - 1200) / 2 * 0.58   # ≈ 2233
    ry_up = (6850 -  850) / 2 * 0.58   # ≈ 1740
    # Angles matching real Kia Center layout (101=west/left, 106=north/top, 115=south/bottom)
    lower_angles = {
        "101": 180, "102": 157, "103": 140, "104": 123,
        "105": 107,  "106": 90,  "107": 73,  "108": 57,
        "109":  40, "110":  22,  "111":   4, "112": -14,
        "113": -31, "114": -47, "115": -63, "116": -80,
        "117":-100, "118":-122,
    }
    for sec, angle in lower_angles.items():
        a = math.radians(angle)
        cx = cx0 + rx_up * math.cos(a)
        cy = cy0 + ry_up * math.sin(a)
        smap[sec] = to_svg(cx, cy)

    # ── 109A / 110A / 111A: push outward from base section ────────────────────
    for base, ext in [("109", "109A"), ("110", "110A"), ("111", "111A")]:
        bx, by = smap[base]
        dx = bx - ARENA_SX; dy = by - ARENA_SY
        mag = math.sqrt(dx*dx + dy*dy) or 1
        smap[ext] = (round(bx + dx/mag * 18, 1), round(by + dy/mag * 18, 1))

    # ── Club sections C-A through C-F (upper concourse, east side) ────────────
    # In real map: C-A to C-F run along the east (right) side, top to bottom
    club_angles = {
        "C-A": 30, "C-B": 18, "C-C": 6,
        "C-D": -6, "C-E": -18, "C-F": -30,
    }
    rx_c = rx_up * 0.80; ry_c = ry_up * 0.75
    for lbl, angle in club_angles.items():
        a = math.radians(angle)
        smap[lbl] = to_svg(cx0 + rx_c * math.cos(a), cy0 + ry_c * math.sin(a))

    return smap


# ── Dot scatter (sunflower spiral) ────────────────────────────────────────────

GOLDEN = 2.39996323

def sunflower_dots(cx, cy, seats, spread=15):
    n = len(seats)
    dots = []
    for i, seat in enumerate(seats):
        r = spread * math.sqrt(i / max(n - 1, 1))
        angle = i * GOLDEN
        x = round(cx + r * math.cos(angle), 1)
        y = round(cy + r * math.sin(angle), 1)
        is_ns, price = seat
        dots.append([x, y, is_ns, price])
    return dots


# ── Data loading ───────────────────────────────────────────────────────────────

def load_game(gdir, smap):
    ns_keys = set()
    with open(f"{gdir}/no_shows.csv", newline="") as f:
        for r in csv.DictReader(f):
            ns_keys.add((r["section"].strip(), r["row"].strip(), r["seat"].strip()))

    by_sec = defaultdict(list)
    with open(f"{gdir}/pre_game.csv", newline="") as f:
        for r in csv.DictReader(f):
            sec  = r["section"].strip()
            row  = r["row"].strip()
            seat = r["seat"].strip()
            try:    price = round(float(r["price_usd"]))
            except: price = 0
            by_sec[sec].append([(1 if (sec, row, seat) in ns_keys else 0), price])

    dots = []
    for sec, seats in by_sec.items():
        if sec not in smap:
            continue
        cx, cy = smap[sec]
        spread = max(9, min(24, 2.5 * math.sqrt(len(seats))))
        dots.extend(sunflower_dots(cx, cy, seats, spread))

    ns_seats = [d for d in dots if d[2] == 1]
    return {
        "dots":  dots,
        "n_pre": len(dots),
        "n_ns":  len(ns_seats),
        "dead":  sum(d[3] for d in ns_seats),
    }


# ── HTML generation ────────────────────────────────────────────────────────────

def gen_html(games_data, arena_svg_content):
    games_js = json.dumps([
        {"label": g["label"], "n_pre": g["n_pre"],
         "n_ns": g["n_ns"], "dead": g["dead"], "dots": g["dots"]}
        for g in games_data
    ])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Magic · Empty Seats</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#05070d;--f:'Inter',sans-serif;
  --orange:#ff6b2b;--dim:rgba(160,185,230,.35);--border:rgba(255,255,255,.07);
}}
html,body{{font-family:var(--f);background:var(--bg);color:#e8eeff;
  height:100%;overflow:hidden;-webkit-font-smoothing:antialiased;}}
#shell{{display:flex;flex-direction:column;height:100vh;}}

#top{{flex:none;height:48px;display:flex;align-items:center;
  justify-content:space-between;padding:0 24px;
  border-bottom:1px solid var(--border);background:rgba(5,7,13,.98);z-index:10;}}
#brand{{font-size:.7rem;font-weight:800;letter-spacing:.22em;
  text-transform:uppercase;color:rgba(200,220,255,.38);}}
#toggle{{display:flex;gap:6px;}}
.tog{{font-family:var(--f);font-size:.65rem;font-weight:600;letter-spacing:.05em;
  background:transparent;border:1px solid var(--border);color:var(--dim);
  padding:4px 13px;border-radius:999px;cursor:pointer;transition:all .18s;}}
.tog.on{{color:#fff;background:rgba(255,107,43,.15);border-color:rgba(255,107,43,.5);}}
.tog:hover:not(.on){{border-color:rgba(255,255,255,.18);color:#fff;}}

#stage{{flex:1;display:flex;flex-direction:column;align-items:center;
  padding:10px 0 0;gap:6px;overflow:hidden;min-height:0;}}

#hl{{flex:none;text-align:center;}}
#hl h2{{font-size:1.1rem;font-weight:800;letter-spacing:-.03em;color:#fff;margin-bottom:2px;}}
#hl p{{font-size:.62rem;color:var(--dim);}}
.hi{{color:var(--orange);}}

#arena-wrap{{flex:1;width:100%;display:flex;align-items:center;
  justify-content:center;overflow:hidden;min-height:0;padding:0 8px;position:relative;}}

#svg-container{{position:relative;}}
#arena-bg{{display:block;width:100%;height:100%;}}
#dot-layer{{position:absolute;top:0;left:0;width:100%;height:100%;overflow:visible;}}

.ds{{opacity:0;transition:opacity .3s;}}
.dn{{opacity:0;}}
@keyframes pop{{0%{{opacity:0;r:.5}}60%{{opacity:1;r:5}}100%{{opacity:1;r:3.5}}}}
@keyframes breathe{{0%,100%{{opacity:.72}}50%{{opacity:1}}}}
.dn.lit{{
  animation:pop .3s ease forwards,breathe 2.8s ease-in-out .3s infinite;
  filter:drop-shadow(0 0 4px rgba(255,107,43,.75));
}}

#stats{{flex:none;display:flex;align-items:stretch;justify-content:center;
  height:52px;border-top:1px solid var(--border);background:rgba(5,7,13,.98);}}
.stat{{display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:0 26px;border-right:1px solid var(--border);gap:1px;}}
.stat:last-child{{border-right:none;}}
.sv{{font-size:.95rem;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums;}}
.sl{{font-size:.5rem;font-weight:600;letter-spacing:.13em;text-transform:uppercase;color:var(--dim);}}
.or{{color:var(--orange);}}

#legend{{position:absolute;right:18px;bottom:60px;
  display:flex;flex-direction:column;gap:6px;pointer-events:none;}}
.lrow{{display:flex;align-items:center;gap:7px;}}
.ld{{width:8px;height:8px;border-radius:50%;flex:none;}}
.ld.ns{{background:var(--orange);box-shadow:0 0 5px rgba(255,107,43,.6);}}
.ld.sd{{background:rgba(70,130,220,.5);}}
.ll{{font-size:.56rem;font-weight:500;color:var(--dim);}}
</style>
</head>
<body>
<div id="shell">
  <div id="top">
    <span id="brand">Magic &nbsp;·&nbsp; Kia Center</span>
    <div id="toggle"></div>
  </div>
  <div id="stage">
    <div id="hl">
      <h2>Every empty seat. <span class="hi">Top-down view.</span></h2>
      <p>Each dot = one ticket still available at halftime &mdash; positioned by real section coordinates.</p>
    </div>
    <div id="arena-wrap">
      <div id="svg-container">
        <!-- Real TM arena SVG as background -->
        <svg id="arena-bg" viewBox="0 0 {SVG_W} {SVG_H}"
             xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
          <!-- Darken the arena SVG so dots pop -->
          <defs>
            <filter id="darken">
              <feColorMatrix type="matrix"
                values="0.28 0 0 0 0.02
                        0    0.28 0 0 0.03
                        0    0 0.32 0 0.06
                        0    0 0    1 0"/>
            </filter>
          </defs>
          <g filter="url(#darken)" transform="scale({SVG_W/10240:.6f} {SVG_H/7680:.6f})">
            {arena_svg_content}
          </g>
        </svg>
        <!-- Dot overlay -->
        <svg id="dot-layer" viewBox="0 0 {SVG_W} {SVG_H}"
             xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
          <g id="g-sold"></g>
          <g id="g-ns"></g>
        </svg>
      </div>
    </div>
  </div>
  <div id="stats">
    <div class="stat"><span class="sv" id="sv-pre">—</span><span class="sl">Pre-game seats</span></div>
    <div class="stat"><span class="sv or" id="sv-ns">—</span><span class="sl">Empty at halftime</span></div>
    <div class="stat"><span class="sv or" id="sv-rt">—%</span><span class="sl">No-show rate</span></div>
    <div class="stat"><span class="sv or" id="sv-dv">$—</span><span class="sl">Dead inventory</span></div>
  </div>
</div>

<div id="legend">
  <div class="lrow"><div class="ld ns"></div><span class="ll">Empty at halftime</span></div>
  <div class="lrow"><div class="ld sd"></div><span class="ll">Sold / present</span></div>
</div>

<script>
const GAMES = {games_js};

const tog = document.getElementById('toggle');
GAMES.forEach((g, i) => {{
  const b = document.createElement('button');
  b.className = 'tog' + (i === 0 ? ' on' : '');
  b.textContent = g.label;
  b.onclick = () => show(i);
  tog.appendChild(b);
}});

// Size the dot layer to match the arena background
function syncSize() {{
  const bg  = document.getElementById('arena-bg');
  const dot = document.getElementById('dot-layer');
  const con = document.getElementById('svg-container');
  const wrap = document.getElementById('arena-wrap');

  // Fit the arena SVG in the wrap, preserving 4:3 aspect
  const ww = wrap.clientWidth  - 16;
  const wh = wrap.clientHeight - 8;
  const ar = {SVG_W} / {SVG_H};
  let w, h;
  if (ww / wh > ar) {{ h = wh; w = h * ar; }}
  else              {{ w = ww; h = w / ar; }}

  con.style.width  = w + 'px';
  con.style.height = h + 'px';
  bg.style.width   = w + 'px';
  bg.style.height  = h + 'px';
  dot.style.width  = w + 'px';
  dot.style.height = h + 'px';
}}
syncSize();
window.addEventListener('resize', syncSize);

const NS    = document.createElementNS.bind(document, 'http://www.w3.org/2000/svg');
const gSold = document.getElementById('g-sold');
const gNs   = document.getElementById('g-ns');

function makeDot(x, y, isNs) {{
  const c = NS('circle');
  c.setAttribute('cx', x); c.setAttribute('cy', y);
  c.setAttribute('r', isNs ? 3.5 : 2.5);
  c.setAttribute('fill', isNs ? '#ff6b2b' : 'rgba(70,130,220,.3)');
  c.setAttribute('class', isNs ? 'dn' : 'ds');
  return c;
}}

function show(idx) {{
  document.querySelectorAll('.tog').forEach((b, i) => b.classList.toggle('on', i === idx));
  gSold.innerHTML = ''; gNs.innerHTML = '';

  const g = GAMES[idx];
  const soldDots = [], nsDots = [];
  for (const d of g.dots) {{
    const dot = makeDot(d[0], d[1], d[2] === 1);
    (d[2] === 1 ? nsDots : soldDots).push(dot);
  }}
  soldDots.forEach(d => gSold.appendChild(d));
  nsDots.forEach(d  => gNs.appendChild(d));

  // Phase 1: sold seats fade in
  requestAnimationFrame(() => soldDots.forEach(d => d.style.opacity = '1'));

  // Phase 2: no-shows sweep section by section
  const sorted = [...nsDots].sort((a, b) =>
    Math.atan2(+a.getAttribute('cy') - {ARENA_SY}, +a.getAttribute('cx') - {ARENA_SX}) -
    Math.atan2(+b.getAttribute('cy') - {ARENA_SY}, +b.getAttribute('cx') - {ARENA_SX})
  );
  const sweep = 1600;
  sorted.forEach((d, i) => {{
    setTimeout(() => d.classList.add('lit'),
      400 + (i / Math.max(sorted.length - 1, 1)) * sweep);
  }});

  // Stats count-up
  const rate = g.n_pre > 0 ? g.n_ns / g.n_pre : 0;
  countUp('sv-pre', g.n_pre, '',  0, 900, false);
  countUp('sv-ns',  g.n_ns,  '',  0, 900, false);
  countUp('sv-rt',  +(rate*100).toFixed(1), '%', 1, 900, false);
  countUp('sv-dv',  g.dead, '', 0, 900, true);
}}

function countUp(id, target, suffix, dec, dur, money) {{
  const el = document.getElementById(id);
  const t0 = Date.now();
  const step = () => {{
    const p = Math.min((Date.now()-t0)/dur, 1);
    const e = 1 - Math.pow(1-p, 3);
    const v = target * e;
    el.textContent = money ? '$'+Math.round(v).toLocaleString()+suffix
      : dec ? v.toFixed(dec)+suffix : Math.round(v).toLocaleString()+suffix;
    if (p < 1) requestAnimationFrame(step);
  }};
  requestAnimationFrame(step);
}}

show(0);
</script>
</body>
</html>"""


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    smap = build_section_map()

    # Load the real TM arena SVG — strip the outer <svg> tag so we can embed it
    with open("data/magic/arena.svg", encoding="utf-8") as f:
        raw = f.read()
    # Extract inner content (everything inside the root <svg>...</svg>)
    import re
    inner = re.sub(r'^<svg[^>]*>', '', raw, count=1).rstrip()
    if inner.endswith("</svg>"):
        inner = inner[:-6]
    arena_svg_content = inner

    games_data = []
    for gdir, label in GAMES:
        if not (os.path.isfile(f"{gdir}/pre_game.csv") and
                os.path.isfile(f"{gdir}/no_shows.csv")):
            print(f"Skipping {label} — missing CSVs")
            continue
        data = load_game(gdir, smap)
        data["label"] = label
        games_data.append(data)
        rate = data["n_ns"] / data["n_pre"] if data["n_pre"] else 0
        print(f"{label}: {data['n_pre']} pre, {data['n_ns']} no-shows ({rate:.1%}), dead=${data['dead']:,.0f}")

    if not games_data:
        print("No games found.")
        return

    os.makedirs("output", exist_ok=True)
    out = "output/magic_seat_story.html"
    with open(out, "w") as f:
        f.write(gen_html(games_data, arena_svg_content))
    print(f"\nSaved → {out}")
    os.system(f"open {out}")


if __name__ == "__main__":
    main()
