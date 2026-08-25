"""
generate_bulls_story.py  —  TM-style Bulls seat visualization

Shows ALL seats as dots (from geometry), colored:
  - gray:   seat exists, not in our secondary market data
  - blue:   tracked seat, sold (present at halftime)
  - red:    tracked seat, still available at halftime (no-show)

Click a section → zoom in. Click background → zoom out.
"""
import csv, json, math, os, re
from collections import defaultdict

GAME_DIR   = "data/bulls/2026-04-01_indiana_pacers_at_bulls"
GEO_PATH   = "data/bulls/seatmap_geo.json"
DOM_SVG    = "data/bulls/arena.svg"
BG_SVG     = "data/bulls/arena_full.svg"
GAME_LABEL = "vs Pacers · Apr 1"

SVG_W, SVG_H = 960, 720
TM_W,  TM_H  = 10240.0, 7680.0
SX = SVG_W / TM_W
SY = SVG_H / TM_H


def load_section_paths():
    with open(DOM_SVG) as f:
        content = f.read()
    paths = re.findall(r'<path([^>]*data-section-name[^>]*)/?>', content, re.DOTALL)
    out = {}
    for p in paths:
        nm = re.search(r'data-section-name="([^"]+)"', p)
        d  = re.search(r'\bd="([^"]+)"', p)
        if nm and d:
            out[nm.group(1)] = d.group(1)
    return out


def load_game():
    ns_keys = set()
    with open(f"{GAME_DIR}/no_shows.csv", newline="") as f:
        for r in csv.DictReader(f):
            ns_keys.add((r["section"].strip(), r["row"].strip(), r["seat"].strip()))

    pre_keys = set()
    pre_price = {}
    with open(f"{GAME_DIR}/pre_game.csv", newline="") as f:
        for r in csv.DictReader(f):
            sec, row, seat = r["section"].strip(), r["row"].strip(), r["seat"].strip()
            key = (sec, row, seat)
            pre_keys.add(key)
            try: pre_price[key] = round(float(r["price_usd"]))
            except: pre_price[key] = 0

    return ns_keys, pre_keys, pre_price


def load_geo(ns_keys, pre_keys, pre_price):
    with open(GEO_PATH) as f:
        geo = json.load(f)
    page = geo.get("pages", [geo])[0]

    # section → list of dot dicts
    sections = {}

    def extract_composite(seg):
        name = seg.get("name", "")
        dots = []
        for child in seg.get("segments", []):
            row_name = child.get("name", "")
            # ROW level
            for p in child.get("placesNoKeys", []):
                if len(p) >= 4:
                    key = (name, row_name, str(p[1]))
                    status = 2 if key in ns_keys else (1 if key in pre_keys else 0)
                    price  = pre_price.get(key, 0)
                    dots.append({
                        "x": round(p[2] * SX, 2),
                        "y": round(p[3] * SY, 2),
                        "s": status,   # 0=gray 1=blue(sold) 2=red(ns)
                        "p": price,
                        "r": row_name,
                        "n": str(p[1]),
                    })
            # SECTION level (some venues nest deeper)
            for grandchild in child.get("segments", []):
                gname = grandchild.get("name", "")
                for p in grandchild.get("placesNoKeys", []):
                    if len(p) >= 4:
                        key = (name, gname, str(p[1]))
                        status = 2 if key in ns_keys else (1 if key in pre_keys else 0)
                        price  = pre_price.get(key, 0)
                        dots.append({
                            "x": round(p[2] * SX, 2),
                            "y": round(p[3] * SY, 2),
                            "s": status,
                            "p": price,
                            "r": gname,
                            "n": str(p[1]),
                        })
        if dots:
            sections[name] = dots

    for seg in page.get("segments", []):
        if seg.get("segmentCategory") == "COMPOSITE":
            extract_composite(seg)
        for child in seg.get("segments", []):
            if child.get("segmentCategory") == "COMPOSITE":
                extract_composite(child)

    return sections   # {sec_name: [dot, ...]}


def load_bg_inner():
    with open(BG_SVG, encoding="utf-8") as f:
        raw = f.read()
    inner = re.sub(r'^<svg[^>]*>', '', raw, count=1).rstrip()
    if inner.endswith("</svg>"):
        inner = inner[:-6]
    return inner


def gen_html(sec_paths, geo_sections, ns_keys, pre_keys, pre_price, bg_inner):
    sec_paths_js = json.dumps(sec_paths, separators=(',', ':'))
    # Per-section summary stats
    sec_stats = {}
    for sec, dots in geo_sections.items():
        ns_dots  = [d for d in dots if d["s"] == 2]
        pre_dots = [d for d in dots if d["s"] >= 1]
        if not pre_dots and not ns_dots:
            continue
        total_pre = len(pre_dots) + len(ns_dots) - len(ns_dots)  # pre = s==1 + s==2... wait
        pre_count = sum(1 for d in dots if d["s"] == 1) + len(ns_dots)
        sec_stats[sec] = {
            "pre":  pre_count,
            "ns":   len(ns_dots),
            "rate": len(ns_dots) / pre_count if pre_count else 0,
            "dead": sum(d["p"] for d in ns_dots),
        }

    total_pre  = sum(v["pre"]  for v in sec_stats.values())
    total_ns   = sum(v["ns"]   for v in sec_stats.values())
    total_dead = sum(v["dead"] for v in sec_stats.values())
    total_rate = total_ns / total_pre if total_pre else 0

    # Pack all dots into one flat JS array per section
    # Format: [x, y, status]  status: 0=gray, 1=blue, 2=red
    # Round to 1 decimal to reduce file size
    sections_js = {}
    for sec, dots in geo_sections.items():
        stats = sec_stats.get(sec, {"pre": 0, "ns": 0, "rate": 0, "dead": 0})
        # For no-show dots include [x, y, 2, row, seat, price]
        # For others just [x, y, status] to keep file small
        packed = []
        for d in dots:
            if d["s"] == 2:
                packed.append([round(d["x"], 1), round(d["y"], 1), 2,
                                d["r"], d["n"], d["p"]])
            else:
                packed.append([round(d["x"], 1), round(d["y"], 1), d["s"]])
        sections_js[sec] = {
            "dots":  packed,
            "pre":   stats["pre"],
            "ns":    stats["ns"],
            "rate":  round(stats["rate"], 3),
            "dead":  stats["dead"],
        }

    sections_json = json.dumps(sections_js, separators=(',', ':'))

    # Section path overlays — very subtle, just the outlines
    overlay_paths = []
    for sec, d_attr in sec_paths.items():
        has_data = sec in sec_stats and sec_stats[sec]["ns"] > 0
        overlay_paths.append(
            f'<path class="sec-hit" data-sec="{sec}" '
            f'd="{d_attr}" '
            f'fill="transparent" '
            f'stroke="rgba(255,255,255,0)" stroke-width="0" '
            f'style="cursor:pointer" '
            f'transform="scale({SX:.7f},{SY:.7f})"/>'
        )

    overlay_svg = "\n          ".join(overlay_paths)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Bulls · Empty Seats</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#05070d;--f:'Inter',sans-serif;
  --red:#ce1141;--dim:rgba(200,185,185,.4);--border:rgba(255,255,255,.07);
  --blue:#4a90d9;--gray:rgba(180,180,180,.18);
}}
html,body{{font-family:var(--f);background:var(--bg);color:#f0e8e8;
  height:100%;overflow:hidden;-webkit-font-smoothing:antialiased;}}
#shell{{display:flex;flex-direction:column;height:100vh;}}

#top{{flex:none;height:48px;display:flex;align-items:center;gap:14px;
  padding:0 20px;border-bottom:1px solid var(--border);
  background:rgba(5,7,13,.98);z-index:10;}}
#brand{{font-size:.7rem;font-weight:800;letter-spacing:.2em;
  text-transform:uppercase;color:rgba(220,180,180,.4);flex:none;}}
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

/* Seat dots — all same radius so they tile cleanly at any zoom */
circle.gray{{ fill:rgba(160,160,180,.22); }}
circle.blue{{ fill:rgba(74,144,217,.75); }}
circle.red{{  fill:#ce1141; filter:drop-shadow(0 0 1.5px rgba(206,17,65,.8)); }}
circle.red:hover{{ filter:drop-shadow(0 0 3px rgba(206,17,65,1)); }}

/* Section highlight on zoom */
.sec-active{{
  fill:none;stroke:rgba(255,255,255,.55);stroke-width:6;
  pointer-events:none;
}}

/* Invisible hit areas for click */
.sec-hit{{pointer-events:fill;}}
.sec-hit:hover + .sec-hover-ring, .sec-hit:hover{{
  /* handled in JS */
}}

#stats{{flex:none;display:flex;align-items:stretch;justify-content:center;
  height:50px;border-top:1px solid var(--border);background:rgba(5,7,13,.98);}}
.stat{{display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:0 24px;border-right:1px solid var(--border);gap:1px;}}
.stat:last-child{{border-right:none;}}
.sv{{font-size:.9rem;font-weight:800;letter-spacing:-.02em;font-variant-numeric:tabular-nums;}}
.sl{{font-size:.48rem;font-weight:600;letter-spacing:.12em;text-transform:uppercase;color:var(--dim);}}
.rd{{color:var(--red);}}

#legend{{position:absolute;left:14px;bottom:58px;
  display:flex;flex-direction:column;gap:5px;pointer-events:none;}}
.lrow{{display:flex;align-items:center;gap:7px;}}
.ld{{width:9px;height:9px;border-radius:50%;flex:none;}}
.ll{{font-size:.54rem;font-weight:500;color:var(--dim);}}

/* Section tooltip */
#tooltip{{position:fixed;pointer-events:none;
  background:rgba(8,12,24,.94);border:1px solid rgba(255,255,255,.1);
  border-radius:8px;padding:9px 13px;font-size:.62rem;
  display:none;z-index:30;max-width:200px;}}
#tooltip.show{{display:block;}}
#tt-sec{{font-weight:700;font-size:.78rem;margin-bottom:4px;color:#fff;}}
#tt-body{{color:var(--dim);line-height:1.65;}}

/* Seat-level tooltip */
#seat-tooltip{{position:fixed;pointer-events:none;
  background:rgba(8,12,24,.96);border:1px solid rgba(206,17,65,.35);
  border-radius:8px;padding:8px 12px;display:none;z-index:40;
  white-space:nowrap;}}
#seat-tooltip.show{{display:block;}}
.st-sec{{font-size:.68rem;font-weight:700;color:#fff;margin-bottom:3px;}}
.st-tag{{font-size:.58rem;color:#ce1141;font-weight:600;margin-bottom:2px;}}
.st-price{{font-size:.6rem;color:var(--dim);}}
</style>
</head>
<body>
<div id="shell">
  <div id="top">
    <span id="brand">Bulls · United Center</span>
    <button id="back-btn" onclick="resetView()">← All sections</button>
    <span id="sec-label"></span>
    <span id="sec-sub"></span>
  </div>

  <div id="arena-wrap">
    <svg id="main-svg" viewBox="0 0 {SVG_W} {SVG_H}"
         xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
      <defs>
        <filter id="dk">
          <feColorMatrix type="matrix"
            values="0.32 0 0 0 0.02
                    0    0.23 0 0 0.02
                    0    0 0.27 0 0.05
                    0    0 0    1 0"/>
        </filter>
      </defs>

      <!-- Arena background -->
      <g id="bg" filter="url(#dk)"
         transform="scale({SX:.7f},{SY:.7f})">
        {bg_inner}
      </g>

      <!-- Gray + blue seat dots (below hit areas) -->
      <g id="dots-bg"></g>

      <!-- Invisible section hit areas -->
      <g id="sec-hits">
        {overlay_svg}
      </g>

      <!-- Red no-show dots (above hit areas so they receive hover) -->
      <g id="dots-ns"></g>

      <!-- Hover highlight -->
      <g id="hover-ring"></g>

    </svg>
  </div>

  <div id="stats">
    <div class="stat"><span class="sv" id="sv-pre">{total_pre:,}</span><span class="sl">Secondary mkt seats</span></div>
    <div class="stat"><span class="sv rd" id="sv-ns">{total_ns:,}</span><span class="sl">Empty at halftime</span></div>
    <div class="stat"><span class="sv rd" id="sv-rt">{total_rate:.1%}</span><span class="sl">No-show rate</span></div>
    <div class="stat"><span class="sv rd" id="sv-dv">${total_dead:,.0f}</span><span class="sl">Dead inventory</span></div>
  </div>
</div>

<div id="legend">
  <div class="lrow"><div class="ld" style="background:rgba(160,160,180,.3)"></div><span class="ll">All seats</span></div>
  <div class="lrow"><div class="ld" style="background:rgba(74,144,217,.7)"></div><span class="ll">Tracked sold</span></div>
  <div class="lrow"><div class="ld" style="background:#ce1141;box-shadow:0 0 5px rgba(206,17,65,.6)"></div><span class="ll">Empty at halftime</span></div>
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
const TOTAL_PRE  = {total_pre};
const TOTAL_NS   = {total_ns};
const TOTAL_RATE = {total_rate:.4f};
const TOTAL_DEAD = {total_dead};
const SCALE_X    = {SX:.7f};
const SCALE_Y    = {SY:.7f};

const svg      = document.getElementById('main-svg');
const NS       = document.createElementNS.bind(document, 'http://www.w3.org/2000/svg');
const dotsBg   = document.getElementById('dots-bg');
const dotsNs   = document.getElementById('dots-ns');
const tooltip  = document.getElementById('tooltip');
const secDots  = {{}};
let zoomedSec  = null;

// ── Current viewBox state (for smooth pinch/pan) ──────────────────────────────
let vb = {{x:0, y:0, w:SVG_W, h:SVG_H}};
function applyVB() {{
  svg.setAttribute('viewBox', `${{vb.x}} ${{vb.y}} ${{vb.w}} ${{vb.h}}`);
}}

// ── Build all seat dots ───────────────────────────────────────────────────────
const seatTooltip = document.getElementById('seat-tooltip');

for (const [sec, data] of Object.entries(SECS)) {{
  const bgGroup = NS('g');
  const nsGroup = NS('g');
  const circles = [];

  for (const d of data.dots) {{
    const c = NS('circle');
    c.setAttribute('cx', d[0]);
    c.setAttribute('cy', d[1]);
    c.setAttribute('r', '1.2');
    const isNs = d[2] === 2, isSold = d[2] === 1;
    c.setAttribute('class', isNs ? 'red' : (isSold ? 'blue' : 'gray'));
    c.style.opacity = '0';

    if (isNs) {{
      c.dataset.sec   = sec;
      c.dataset.row   = d[3] || '';
      c.dataset.seat  = d[4] || '';
      c.dataset.price = d[5] || 0;
      c.style.cursor  = 'crosshair';
      c.addEventListener('mouseenter', showSeatTooltip);
      c.addEventListener('mousemove',  moveSeatTooltip);
      c.addEventListener('mouseleave', hideSeatTooltip);
      nsGroup.appendChild(c);
    }} else {{
      bgGroup.appendChild(c);
    }}
    circles.push(c);
  }}

  dotsBg.appendChild(bgGroup);
  dotsNs.appendChild(nsGroup);
  secDots[sec] = circles;
}}

// ── Seat-level tooltip ────────────────────────────────────────────────────────
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

// ── Entrance animation — clockwise sweep using rAF batching ──────────────────
(function runEntrance() {{
  const CX = SVG_W / 2, CY = SVG_H / 2;
  const PI2 = Math.PI * 2;
  const START_ANGLE = -Math.PI / 2; // top of arena

  // Sort all dots by clockwise angle from top
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

  // Pre-assign normalized reveal times [0..1] per phase
  const nonNs = allDots.filter(d => !d.isNs);
  const ns    = allDots.filter(d =>  d.isNs);

  // Phase 1: 0–1200ms — all non-red seats fade in clockwise
  // Phase 2: 1300–2200ms — red dots fade in clockwise
  const P1_DUR = 1200, P2_START = 1300, P2_DUR = 900;

  nonNs.forEach((d, i) => d.revealAt = (i / Math.max(nonNs.length - 1, 1)) * P1_DUR);
  ns.forEach((d, i)    => d.revealAt = P2_START + (i / Math.max(ns.length - 1, 1)) * P2_DUR);

  let p1Idx = 0, p2Idx = 0;
  const t0 = performance.now();

  function frame(now) {{
    const elapsed = now - t0;
    while (p1Idx < nonNs.length && nonNs[p1Idx].revealAt <= elapsed) {{
      nonNs[p1Idx].c.style.opacity = '1';
      p1Idx++;
    }}
    while (p2Idx < ns.length && ns[p2Idx].revealAt <= elapsed) {{
      ns[p2Idx].c.style.opacity = '1';
      p2Idx++;
    }}
    if (p1Idx < nonNs.length || p2Idx < ns.length)
      requestAnimationFrame(frame);
  }}
  requestAnimationFrame(frame);
}})();

// ── Tooltip ───────────────────────────────────────────────────────────────────
function showTooltip(sec, e) {{
  const data = SECS[sec];
  if (!data) return;
  document.getElementById('tt-sec').textContent = 'Section ' + sec;
  document.getElementById('tt-body').innerHTML =
    (data.ns > 0
      ? `<span style="color:#ce1141;font-weight:600">${{data.ns}} empty</span> of ${{data.pre}} tracked<br>`
      : `${{data.pre}} tracked seats, 0 empty<br>`) +
    (data.dead > 0 ? `$${{data.dead.toLocaleString()}} dead inventory` : '');
  tooltip.classList.add('show');
  moveTooltip(e);
}}
function moveTooltip(e) {{
  tooltip.style.left = (e.clientX + 16) + 'px';
  tooltip.style.top  = Math.min(e.clientY - 10, window.innerHeight - 110) + 'px';
}}

document.querySelectorAll('.sec-hit').forEach(path => {{
  const sec = path.dataset.sec;
  path.addEventListener('mouseenter', e => showTooltip(sec, e));
  path.addEventListener('mousemove',  e => moveTooltip(e));
  path.addEventListener('mouseleave', () => tooltip.classList.remove('show'));
  path.addEventListener('click',      () => zoomToSection(sec));
}});

// ── Section zoom ──────────────────────────────────────────────────────────────
function zoomToSection(sec) {{
  const data = SECS[sec];
  if (!data || !data.dots.length) return;
  tooltip.classList.remove('show');
  zoomedSec = sec;

  const xs = data.dots.map(d=>d[0]), ys = data.dots.map(d=>d[1]);
  const minX=Math.min(...xs), maxX=Math.max(...xs);
  const minY=Math.min(...ys), maxY=Math.max(...ys);
  const padX = Math.max((maxX-minX)*0.45, 18);
  const padY = Math.max((maxY-minY)*0.45, 18);
  let w = (maxX-minX)+padX*2, h = (maxY-minY)+padY*2;
  const ar = SVG_W/SVG_H;
  if (w/h > ar) h = w/ar; else w = h*ar;
  const tx = (minX+maxX)/2-w/2, ty = (minY+maxY)/2-h/2;

  animVB(vb, {{x:tx,y:ty,w,h}}, 380);

  // Highlight ring
  const ring = document.getElementById('hover-ring');
  ring.innerHTML = '';
  if (SEC_PATHS[sec]) {{
    const p = NS('path');
    p.setAttribute('d', SEC_PATHS[sec]);
    p.setAttribute('class', 'sec-active');
    p.setAttribute('transform', `scale(${{SCALE_X}},${{SCALE_Y}})`);
    ring.appendChild(p);
  }}

  document.getElementById('back-btn').classList.add('show');
  document.getElementById('sec-label').textContent = 'Section ' + sec;
  document.getElementById('sec-label').classList.add('show');
  document.getElementById('sec-sub').textContent = data.ns > 0
    ? `${{data.ns}} empty · ${{data.pre}} tracked · ${{(data.rate*100).toFixed(0)}}% no-show`
    : `${{data.pre}} tracked · 0 empty at halftime`;
  document.getElementById('sec-sub').classList.add('show');
  document.getElementById('sv-pre').textContent = data.pre.toLocaleString();
  document.getElementById('sv-ns').textContent  = data.ns.toLocaleString();
  document.getElementById('sv-rt').textContent  = (data.rate*100).toFixed(0)+'%';
  document.getElementById('sv-dv').textContent  = data.dead>0 ? '$'+data.dead.toLocaleString() : '$0';
}}

function resetView() {{
  zoomedSec = null;
  animVB(vb, {{x:0,y:0,w:SVG_W,h:SVG_H}}, 360);
  document.getElementById('hover-ring').innerHTML = '';
  document.getElementById('back-btn').classList.remove('show');
  document.getElementById('sec-label').classList.remove('show');
  document.getElementById('sec-sub').classList.remove('show');
  document.getElementById('sv-pre').textContent = TOTAL_PRE.toLocaleString();
  document.getElementById('sv-ns').textContent  = TOTAL_NS.toLocaleString();
  document.getElementById('sv-rt').textContent  = (TOTAL_RATE*100).toFixed(1)+'%';
  document.getElementById('sv-dv').textContent  = '$'+TOTAL_DEAD.toLocaleString();
}}

svg.addEventListener('click', e => {{ if (zoomedSec && e.target===svg) resetView(); }});

// ── Smooth viewBox animation ──────────────────────────────────────────────────
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

// ── Trackpad / mouse: pinch-to-zoom + pan ────────────────────────────────────
// Convert client point to SVG coordinates
function clientToSVG(cx, cy) {{
  const rect = svg.getBoundingClientRect();
  return {{
    x: vb.x + (cx - rect.left) / rect.width  * vb.w,
    y: vb.y + (cy - rect.top)  / rect.height * vb.h,
  }};
}}

svg.addEventListener('wheel', e => {{
  e.preventDefault();
  if (animRaf) {{ cancelAnimationFrame(animRaf); animRaf = null; }}

  const origin = clientToSVG(e.clientX, e.clientY);

  if (e.ctrlKey) {{
    // Pinch-to-zoom (trackpad pinch sends ctrlKey+wheel)
    const factor = 1 + e.deltaY * 0.008;
    const newW   = Math.min(Math.max(vb.w * factor, 30), SVG_W * 4);
    const newH   = newW * (SVG_H / SVG_W);
    // Keep origin point fixed under cursor
    vb.x = origin.x - (origin.x - vb.x) * (newW / vb.w);
    vb.y = origin.y - (origin.y - vb.y) * (newH / vb.h);
    vb.w = newW; vb.h = newH;
  }} else {{
    // Two-finger scroll = pan
    const dx = e.deltaX / svg.getBoundingClientRect().width  * vb.w;
    const dy = e.deltaY / svg.getBoundingClientRect().height * vb.h;
    vb.x += dx; vb.y += dy;
  }}

  // Clamp so you can't pan completely off the map
  const maxX = SVG_W*2, maxY = SVG_H*2;
  vb.x = Math.max(-SVG_W/2, Math.min(vb.x, maxX));
  vb.y = Math.max(-SVG_H/2, Math.min(vb.y, maxY));

  applyVB();
}}, {{passive: false}});

// Mouse drag to pan
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

// ── Fit SVG to window ─────────────────────────────────────────────────────────
function fitSvg() {{
  const wrap = document.getElementById('arena-wrap');
  const ww = wrap.clientWidth, wh = wrap.clientHeight;
  const ar = SVG_W / SVG_H;
  const w = ww/wh > ar ? wh*ar : ww;
  svg.style.width  = w + 'px';
  svg.style.height = (w/ar) + 'px';
}}
fitSvg();
window.addEventListener('resize', fitSvg);
</script>
</body>
</html>"""


def main():
    print("Loading section paths...")
    sec_paths = load_section_paths()

    print("Loading game data...")
    ns_keys, pre_keys, pre_price = load_game()

    print("Loading geometry (all seats)...")
    geo_sections = load_geo(ns_keys, pre_keys, pre_price)
    total_dots = sum(len(v) for v in geo_sections.values())
    print(f"  {len(geo_sections)} sections, {total_dots:,} total seat dots")

    print("Loading arena background...")
    bg_inner = load_bg_inner()

    os.makedirs("output", exist_ok=True)
    out = "output/bulls_seat_story.html"
    with open(out, "w") as f:
        f.write(gen_html(sec_paths, geo_sections, ns_keys, pre_keys, pre_price, bg_inner))
    print(f"Saved → {out}")
    os.system(f"open {out}")


if __name__ == "__main__":
    main()
