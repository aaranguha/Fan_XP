"""
build_stadium_seatmap.py

Turns a team's data/{slug}/seatmap_extract.json + arena_full.svg into the
interactive seat-map page (flat colored section overview, click a section to
zoom in and see real individual seats, red = confirmed empty right now).

Usage:
    python3 build_stadium_seatmap.py <team_slug>

Output:
    docs/nfl_{slug}_seatmap.html
"""

import json
import os
import re
import sys

from nfl_teams import NFL_TEAMS
from generate_nfl_story import NFL_COLORS, NFL_ARENAS


def darken(hex_color: str, factor: float = 0.55) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (max(0, int(c * factor)) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


def main():
    if len(sys.argv) != 2:
        print("Usage: python3 build_stadium_seatmap.py <team_slug>")
        sys.exit(1)
    slug = sys.argv[1]

    if slug not in NFL_TEAMS:
        print(f"ERROR: unknown team slug '{slug}'")
        sys.exit(1)

    extract_path = f"data/{slug}/seatmap_extract.json"
    arena_path = f"data/{slug}/arena_full.svg"
    if not os.path.isfile(extract_path) or not os.path.isfile(arena_path):
        print(f"ERROR: missing {extract_path} or {arena_path}. "
              f"Run fetch_geometry.py then extract_stadium_geo.py for {slug} first.")
        sys.exit(1)

    extract = json.load(open(extract_path))
    FULL_JSON = json.dumps({"secs": extract["secs"]}, separators=(",", ":"))
    META_JSON = json.dumps(
        {"tiers": extract["tiers"], "centroids": extract["centroids"]},
        separators=(",", ":"),
    )

    using_real_data = extract.get("data_source") == "real"
    if using_real_data:
        subline = (
            f"Confirmed empty seats from the {extract.get('game_date', 'most recent')} game. "
            f"Tap a red section to zoom in and see the exact open seats."
        )
        cart_note = "Bidding stays open for 60s after your bid — a higher bid releases your hold and you'll be notified, no charge made."
        legend_dot_label = "Empty &mdash; confirmed no-show"
    else:
        subline = (
            "This team hasn't had a home game yet, so these are illustrative open seats, "
            "not confirmed no-shows. Tap a red section to zoom in."
        )
        cart_note = "Bidding stays open for 60s after your bid — a higher bid releases your hold and you'll be notified, no charge made."
        legend_dot_label = "Empty &mdash; preview only"

    # Different venues' seating bowls have genuinely different real-world
    # proportions (Lambeau Field's bowl is much closer to square/portrait
    # than Levi's Stadium's), but the page's map area is always a landscape
    # box — fitting the viewBox tightly to each bowl's own aspect ratio just
    # makes portrait-shaped venues render as a tall, narrow, letterboxed
    # shape inside that landscape box. Instead, fit to the content with
    # padding, then pad the *shorter* axis further so every venue's frame
    # ends up the same landscape aspect ratio as the page's map area.
    TARGET_ASPECT = 10240 / 7680  # 1.333 — matches the map area's own box

    xs = [p[0] for dots in extract["secs"].values() for p in dots]
    ys = [p[1] for dots in extract["secs"].values() for p in dots]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    pad_x = (max_x - min_x) * 0.16
    pad_y = (max_y - min_y) * 0.16
    vb_x, vb_w = min_x - pad_x, (max_x - min_x) + pad_x * 2
    vb_y, vb_h = min_y - pad_y, (max_y - min_y) + pad_y * 2

    cx, cy = vb_x + vb_w / 2, vb_y + vb_h / 2
    if vb_w / vb_h < TARGET_ASPECT:
        vb_w = vb_h * TARGET_ASPECT
    else:
        vb_h = vb_w / TARGET_ASPECT
    vb_x, vb_y = cx - vb_w / 2, cy - vb_h / 2

    full_vb = {
        "x": round(vb_x, 1), "y": round(vb_y, 1),
        "w": round(vb_w, 1), "h": round(vb_h, 1),
    }
    FULL_VB_JSON = json.dumps(full_vb, separators=(",", ":"))
    VIEWBOX_ATTR = f"{full_vb['x']} {full_vb['y']} {full_vb['w']} {full_vb['h']}"

    arena_raw = open(arena_path, encoding="utf-8").read()
    inner = re.sub(r'^\s*<svg[^>]*>', '', arena_raw)
    inner = re.sub(r'</svg>\s*$', '', inner)
    ARENA_INNER = inner

    # Use the full tm_keyword ("San Francisco 49ers") rather than title-casing
    # the slug — slug.title() mangles "49ers" into "49Ers".
    team_name = NFL_TEAMS[slug]["tm_keyword"]
    arena_name = NFL_ARENAS.get(slug, "")
    city = NFL_TEAMS[slug].get("city", "")
    team_color = NFL_COLORS.get(slug, "#aa0000")
    team_color_dark = darken(team_color)

    HTML = """<title>Second Half Seats</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#e8e9ec;
  --surface:#ffffff;
  --surface-sunken:#f1f2f5;
  --border:#dcdfe4;
  --text:#1c1e22;
  --muted:#6b7280;
  --muted-dim:#9aa0ab;
  --brand:#1454c9;
  --good:#0f8b6c;
  --font:'Inter',system-ui,-apple-system,sans-serif;
  --team:__TEAM_COLOR__;
  --team-dark:__TEAM_COLOR_DARK__;
  --gray-dot:#c7cbd3;
  --sec-standard:#2f66e0;
  --sec-premium:#a9c0ef;
}
html,body{background:var(--bg);color:var(--text);font-family:var(--font);height:100%;-webkit-font-smoothing:antialiased;}
body{min-height:100vh;}
button{font-family:inherit;cursor:pointer;border:none;background:none;}
:focus-visible{outline:2px solid var(--brand);outline-offset:2px}
@media (prefers-reduced-motion: reduce){*{animation-duration:.001ms !important;transition-duration:.001ms !important;}}

#topbar{
  position:sticky;top:0;z-index:30;display:flex;align-items:center;justify-content:space-between;gap:16px;
  padding:14px 26px;background:rgba(255,255,255,.94);backdrop-filter:blur(10px);border-bottom:1px solid var(--border);
}
#eventinfo h1{font-size:1.05rem;font-weight:800;letter-spacing:-.01em;}
#eventinfo p{font-size:.76rem;color:var(--muted);margin-top:1px;}
#livepill{
  display:flex;align-items:center;gap:6px;flex:none;font-size:.66rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
  color:var(--good);background:#e7f6f1;border:1px solid #bfe7da;padding:5px 11px 5px 9px;border-radius:999px;
}
.livedot{width:6px;height:6px;border-radius:50%;background:var(--good);animation:pulse 1.8s ease-in-out infinite;}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}

#shell{display:grid;grid-template-columns:1fr 320px;max-width:1360px;margin:0 auto;}
@media (max-width:920px){#shell{grid-template-columns:1fr;}}

#stage{padding:22px 26px 60px;min-width:0;}
#stagehead{margin-bottom:14px;display:flex;justify-content:space-between;align-items:flex-start;gap:12px;flex-wrap:wrap;}
#stagehead h2{font-size:1.3rem;font-weight:800;letter-spacing:-.01em;margin-bottom:4px;}
#stagehead p{font-size:.82rem;color:var(--muted);max-width:60ch;line-height:1.5;}
#resetbtn{
  display:none;font-size:.74rem;font-weight:700;color:var(--brand);background:var(--surface);
  border:1px solid var(--border);padding:7px 14px;border-radius:8px;flex:none;
}
#resetbtn.show{display:block;}
#seclabel{display:none;font-size:.85rem;font-weight:800;margin-top:2px;}
#seclabel.show{display:block;}

#legend{display:flex;gap:16px;margin:14px 0 16px;flex-wrap:wrap;}
.legend-item{display:flex;align-items:center;gap:6px;font-size:.72rem;color:var(--muted);font-weight:500;}
.legend-swatch{width:10px;height:10px;border-radius:3px;flex:none;}
.legend-swatch.dot{border-radius:50%;width:8px;height:8px;}

#mapwrap{
  position:relative;background:var(--surface);border:1px solid var(--border);
  border-radius:16px;padding:6px;box-shadow:0 1px 2px rgba(15,20,30,.04);overflow:hidden;
}
#bowlsvg{width:100%;height:auto;display:block;border-radius:12px;}

.sec-standard{fill:var(--sec-standard) !important;stroke:#fff !important;stroke-width:6 !important;transition:opacity .12s,fill-opacity .15s;}
.sec-premium{fill:var(--sec-premium) !important;stroke:#fff !important;stroke-width:6 !important;transition:opacity .12s,fill-opacity .15s;}
.sec-path.clickable{cursor:pointer;}
.sec-path.clickable:hover{opacity:.8;}
.sec-path.faded{fill-opacity:.16 !important;}

.section-label{font-family:var(--font);font-size:92px;font-weight:800;fill:#fff;text-anchor:middle;pointer-events:none;}
.section-badge{pointer-events:none;}
.section-badge circle{fill:var(--team);stroke:#fff;stroke-width:8;}
.section-badge text{font-family:var(--font);font-size:44px;font-weight:800;fill:#fff;text-anchor:middle;}

circle.dot-gray{fill:var(--gray-dot);}
circle.dot-open{fill:var(--team);cursor:pointer;}
circle.dot-open:hover{fill:var(--team-dark);}
circle.dot-picked{fill:var(--good) !important;}

#tip{
  position:fixed;pointer-events:none;
  background:rgba(20,22,26,.96);border:1px solid rgba(170,0,0,.35);
  border-radius:8px;padding:9px 13px;display:none;z-index:40;white-space:nowrap;
  box-shadow:0 8px 24px rgba(0,0,0,.28);
}
#tip.show{display:block;}
.tip-sec{font-size:.74rem;font-weight:700;color:#fff;margin-bottom:3px;}
.tip-tag{font-size:.64rem;color:var(--team);font-weight:600;margin-bottom:2px;}
.tip-price{font-size:.66rem;color:rgba(255,255,255,.6);}
.tip-price strong{color:#fff;}

#cart{
  position:sticky;top:65px;align-self:start;padding:24px 22px 26px;border-left:1px solid var(--border);
  min-height:calc(100vh - 65px);display:flex;flex-direction:column;background:var(--surface);
}
@media (max-width:920px){#cart{position:static;border-left:none;border-top:1px solid var(--border);min-height:0;}}
#cart h4{font-size:.7rem;font-weight:800;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);margin-bottom:14px;}
#cart-empty{font-size:.84rem;color:var(--muted);line-height:1.6;padding:20px 0;}
#cart-item{display:none;background:var(--surface-sunken);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px;}
#cart-item.filled{display:block;}
#cart-item .seatname{font-weight:800;font-size:.95rem;}
#cart-item .secname{font-size:.76rem;color:var(--muted);margin-top:2px;}
#bid-note{display:none;font-size:.76rem;color:#b3261e;background:#fbe9e9;border-radius:8px;padding:8px 10px;margin-top:10px;}
#bid-note.show{display:block;}
#bid-label{display:block;font-size:.7rem;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);margin-top:12px;}
#bid-amount{display:block;width:100%;margin-top:6px;padding:9px 10px;border-radius:8px;border:1px solid var(--border);background:var(--surface);color:var(--text);font:inherit;font-size:.92rem;font-weight:700;font-variant-numeric:tabular-nums;}
#bid-amount:focus{outline:none;border-color:var(--brand);}
#cart-divider{border-top:1px solid var(--border);margin:14px 0;}
#cart-row{display:flex;justify-content:space-between;font-size:.82rem;color:var(--muted);margin-bottom:6px;}
#cart-total{display:flex;justify-content:space-between;font-size:1rem;font-weight:800;margin-top:8px;font-variant-numeric:tabular-nums;}
#claimbtn{width:100%;padding:14px;margin-top:auto;border-radius:10px;background:var(--brand);color:#fff;font-weight:700;font-size:.9rem;transition:background .15s,transform .1s;}
#claimbtn:disabled{background:var(--surface-sunken);color:var(--muted-dim);cursor:not-allowed;}
#claimbtn:not(:disabled):hover{background:#0e3f9c;}
#claimbtn:not(:disabled):active{transform:scale(.98);}
#cart-note{font-size:.68rem;color:var(--muted-dim);text-align:center;margin-top:12px;line-height:1.5;}
#fan-fields{display:none;flex-direction:column;gap:8px;margin-bottom:12px;}
#fan-fields.show{display:flex;}
#fan-fields input{width:100%;padding:10px 12px;border-radius:8px;border:1px solid var(--border);background:var(--surface);color:var(--text);font:inherit;font-size:.84rem;}
#fan-fields input:focus{outline:none;border-color:var(--brand);}
#request-status{display:none;margin-top:12px;padding:14px;border-radius:10px;font-size:.82rem;line-height:1.5;}
#request-status.show{display:block;}
#request-status.pending{background:var(--surface-sunken);color:var(--muted);}
#request-status.confirmed{background:#e7f6f1;color:var(--good);}
#request-status.declined{background:#fbe9e9;color:#b3261e;}
#request-status .spin{display:inline-block;width:9px;height:9px;border-radius:50%;border:2px solid currentColor;border-right-color:transparent;animation:spin .7s linear infinite;margin-right:6px;vertical-align:-1px;}
#request-status .code{font-weight:800;font-variant-numeric:tabular-nums;letter-spacing:.04em;}
@keyframes spin{to{transform:rotate(360deg);}}

#toast{
  position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(20px);
  background:var(--text);color:#fff;border-radius:10px;padding:13px 20px;font-size:.82rem;font-weight:600;
  opacity:0;pointer-events:none;transition:opacity .25s,transform .25s;z-index:50;box-shadow:0 12px 32px rgba(0,0,0,.25);
  display:flex;align-items:center;gap:10px;max-width:340px;
}
#toast.show{opacity:1;transform:translateX(-50%) translateY(0);}
#toast .dot{width:7px;height:7px;border-radius:50%;background:var(--good);flex:none;}
</style>

<div id="topbar">
  <div id="eventinfo">
    <h1>__TEAM_NAME__ — Second Half Seats</h1>
    <p>__ARENA_NAME__ &middot; __CITY__</p>
  </div>
  <div id="livepill"><span class="livedot"></span>2nd Half &middot; Live</div>
</div>

<div id="shell">
  <div id="stage">
    <div id="stagehead">
      <div>
        <h2 id="headline">Claim an empty seat</h2>
        <p id="subline">__SUBLINE__</p>
        <div id="seclabel"></div>
      </div>
      <button id="resetbtn">&larr; All sections</button>
    </div>

    <div id="legend">
      <div class="legend-item"><span class="legend-swatch" style="background:var(--sec-standard)"></span>Standard seating</div>
      <div class="legend-item"><span class="legend-swatch" style="background:var(--sec-premium)"></span>Club &amp; VIP</div>
      <div class="legend-item"><span class="legend-swatch dot" style="background:var(--team)"></span>__LEGEND_DOT_LABEL__</div>
    </div>

    <div id="mapwrap">
      <svg id="bowlsvg" viewBox="__VIEWBOX_ATTR__" xmlns="http://www.w3.org/2000/svg">
__ARENA_INNER__
        <g id="labels"></g>
        <g id="badges"></g>
        <g id="dots-gray"></g>
        <g id="dots-open"></g>
      </svg>
      <div id="tip"></div>
    </div>
  </div>

  <div id="cart">
    <h4>Your selection</h4>
    <div id="cart-empty">Tap a section, then tap a red seat to select it.</div>
    <div id="cart-item">
      <div class="seatname" id="cart-seatname">&mdash;</div>
      <div class="secname" id="cart-secname">&mdash;</div>
      <div id="bid-note"></div>
      <label id="bid-label">Your bid<input type="number" id="bid-amount" step="1"></label>
      <div id="cart-divider"></div>
      <div id="cart-row"><span>Bid</span><span id="cart-face">$0</span></div>
      <div id="cart-row"><span>Service fee</span><span id="cart-fee">$0</span></div>
      <div id="cart-total"><span>Total</span><span id="cart-total-amt">$0</span></div>
    </div>
    <div id="fan-fields">
      <input type="text" id="fan-name" placeholder="Full name" autocomplete="name">
      <input type="tel" id="fan-phone" placeholder="Phone (+1XXXXXXXXXX)" autocomplete="tel">
    </div>
    <button id="claimbtn" disabled>Select a seat first</button>
    <div id="request-status"></div>
    <p id="cart-note">__CART_NOTE__</p>
  </div>
</div>

<div id="toast"><span class="dot"></span><span id="toast-text"></span></div>

<script>
const SECS = __FULL_JSON__.secs;      // name -> [[x,y,row,seat,level,isOpen], ...]
const META = __META_JSON__;           // {tiers, centroids}
const ARENA_NAME = __ARENA_NAME_JSON__;
const DEFAULT_SUBLINE = document.getElementById('subline').textContent;
const TEAM_SLUG = __TEAM_SLUG_JSON__;
const API_BASE = __API_BASE_JSON__; // TODO: point at the deployed Railway API URL
const svgNS = 'http://www.w3.org/2000/svg';
const PRICE_MAP = {'80':64,'90':78,'140':118,'160':142,'180':168,'200':195,'220':225,'260':275,'300':320};
function priceFor(level){ return PRICE_MAP[level] || 95; }

const svg = document.getElementById('bowlsvg');
const badgesG = document.getElementById('badges');
const labelsG = document.getElementById('labels');
const dotsGray = document.getElementById('dots-gray');
const dotsOpen = document.getElementById('dots-open');
const FULL_VB = __FULL_VB_JSON__;
let curVB = {...FULL_VB};
let zoomedSec = null;
let pickedSeat = null;

// ── Step 1 (default state): flat, tier-colored real TM section paths ──
Object.keys(SECS).forEach(name => {
  const el = document.getElementById(name);
  if (!el) return;
  const tier = META.tiers[name];
  el.classList.add((tier === 'club' || tier === 'suite') ? 'sec-premium' : 'sec-standard');
  el.classList.add('sec-path', 'clickable');
  el.addEventListener('click', () => zoomToSection(name));

  if (SECS[name].length > 60) {
    const [lcx, lcy] = META.centroids[name] || [0,0];
    const label = document.createElementNS(svgNS, 'text');
    label.setAttribute('x', lcx); label.setAttribute('y', lcy + 15);
    label.setAttribute('class', 'section-label');
    label.textContent = name;
    labelsG.appendChild(label);
  }

  const openCount = SECS[name].filter(d => d[5]).length;
  if (openCount > 0) {
    const [cx, cy] = META.centroids[name] || [0,0];
    const g = document.createElementNS(svgNS, 'g');
    g.setAttribute('class', 'section-badge');
    const c = document.createElementNS(svgNS, 'circle');
    c.setAttribute('cx', cx); c.setAttribute('cy', cy - 130); c.setAttribute('r', 62);
    g.appendChild(c);
    const t = document.createElementNS(svgNS, 'text');
    t.setAttribute('x', cx); t.setAttribute('y', cy - 130 + 16);
    t.textContent = openCount;
    g.appendChild(t);
    badgesG.appendChild(g);
  }
});

const tipEl = document.getElementById('tip');
function showTip(e, secName, rowLabel, num, price){
  tipEl.innerHTML =
    `<div class="tip-sec">Sec ${secName} · Row ${rowLabel} · Seat ${num}</div>` +
    `<div class="tip-tag">Empty — open now</div>` +
    `<div class="tip-price">Face value <strong>$${price}</strong></div>`;
  tipEl.classList.add('show');
  moveTip(e);
}
function moveTip(e){
  const tw = tipEl.offsetWidth;
  const left = Math.min(e.clientX + 14, window.innerWidth - tw - 8);
  tipEl.style.left = left + 'px';
  tipEl.style.top  = Math.min(e.clientY - 10, window.innerHeight - 90) + 'px';
}
function hideTip(){ tipEl.classList.remove('show'); }

function setVB(vb){ svg.setAttribute('viewBox', `${vb.x} ${vb.y} ${vb.w} ${vb.h}`); }

let animRaf = null;
function animVB(from, to, dur, onDone){
  if (animRaf) cancelAnimationFrame(animRaf);
  const t0 = performance.now();
  function frame(now){
    const t = Math.min((now - t0) / dur, 1);
    const e = 1 - Math.pow(1 - t, 3);
    curVB = {
      x: from.x + (to.x - from.x) * e, y: from.y + (to.y - from.y) * e,
      w: from.w + (to.w - from.w) * e, h: from.h + (to.h - from.h) * e,
    };
    setVB(curVB);
    if (t < 1) { animRaf = requestAnimationFrame(frame); }
    else if (onDone) onDone();
  }
  animRaf = requestAnimationFrame(frame);
}

// ── Step 2: on click, zoom in AND reveal that section's exact seats ──
function zoomToSection(name){
  const dots = SECS[name];
  if (!dots || !dots.length) return;
  zoomedSec = name;

  document.querySelectorAll('.sec-path').forEach(el => {
    el.classList.toggle('faded', el.id !== name);
  });
  badgesG.style.display = 'none';
  labelsG.style.display = 'none';

  const xs = dots.map(d => d[0]), ys = dots.map(d => d[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const padX = Math.max((maxX - minX) * 0.35, 60), padY = Math.max((maxY - minY) * 0.35, 60);
  let w = (maxX - minX) + padX * 2, h = (maxY - minY) + padY * 2;
  const ar = FULL_VB.w / FULL_VB.h;
  if (w / h > ar) h = w / ar; else w = h * ar;
  const tx = (minX + maxX) / 2 - w / 2, ty = (minY + maxY) / 2 - h / 2;

  dotsGray.innerHTML = ''; dotsOpen.innerHTML = '';
  animVB(curVB, {x: tx, y: ty, w, h}, 380, () => buildDots(name));

  const openCount = dots.filter(d => d[5]).length;
  document.getElementById('resetbtn').classList.add('show');
  document.getElementById('headline').textContent = 'Section ' + name;
  const lbl = document.getElementById('seclabel');
  lbl.textContent = `${dots.length} seats · ${openCount} open now`;
  lbl.classList.add('show');
  document.getElementById('subline').textContent =
    openCount > 0 ? 'Tap a red seat to select it.' : 'No confirmed openings in this section yet.';
}

function buildDots(name){
  SECS[name].forEach(d => {
    const [x, y, row, num, level, isOpen] = d;
    const c = document.createElementNS(svgNS, 'circle');
    c.setAttribute('cx', x); c.setAttribute('cy', y);
    if (isOpen) {
      c.setAttribute('r', 5.5);
      c.setAttribute('class', 'dot-open');
      c.addEventListener('click', (e) => { e.stopPropagation(); pickSeat(c, name, row, num, priceFor(level)); });
      c.addEventListener('mouseenter', (e) => showTip(e, name, row, num, priceFor(level)));
      c.addEventListener('mousemove', moveTip);
      c.addEventListener('mouseleave', hideTip);
      dotsOpen.appendChild(c);
    } else {
      c.setAttribute('r', 3.2);
      c.setAttribute('class', 'dot-gray');
      dotsGray.appendChild(c);
    }
  });
}

function resetView(){
  zoomedSec = null;
  dotsGray.innerHTML = ''; dotsOpen.innerHTML = '';
  document.querySelectorAll('.sec-path').forEach(el => el.classList.remove('faded'));
  badgesG.style.display = '';
  labelsG.style.display = '';
  animVB(curVB, FULL_VB, 360);
  document.getElementById('resetbtn').classList.remove('show');
  document.getElementById('seclabel').classList.remove('show');
  document.getElementById('headline').textContent = 'Claim an empty seat';
  document.getElementById('subline').textContent = DEFAULT_SUBLINE;
}

document.getElementById('resetbtn').addEventListener('click', resetView);
svg.addEventListener('click', (e) => { if (zoomedSec && e.target === svg) resetView(); });

// Trackpad pinch-out / ctrl+scroll-out while zoomed into a section zooms
// back out to the section overview, matching native map pinch-zoom feel.
document.getElementById('mapwrap').addEventListener('wheel', (e) => {
  if (!zoomedSec) return;
  if (!e.ctrlKey) return;
  if (e.deltaY <= 0) return;
  e.preventDefault();
  resetView();
}, { passive: false });

function updateBidTotals(){
  const bid = Math.max(0, Math.round(Number(document.getElementById('bid-amount').value) || 0));
  const fee = Math.round(bid * 0.12);
  document.getElementById('cart-face').textContent = '$' + bid;
  document.getElementById('cart-fee').textContent = '$' + fee;
  document.getElementById('cart-total-amt').textContent = '$' + (bid + fee);
  return bid;
}

function fillCart(secName, rowLabel, num, minBid, prefillBid){
  document.getElementById('cart-empty').style.display = 'none';
  const item = document.getElementById('cart-item');
  item.classList.add('filled');
  document.getElementById('cart-seatname').textContent = `Row ${rowLabel}, Seat ${num}`;
  document.getElementById('cart-secname').textContent = `Section ${secName} · ${ARENA_NAME}`;
  const bidInput = document.getElementById('bid-amount');
  bidInput.min = minBid;
  bidInput.value = prefillBid != null ? prefillBid : minBid;
  updateBidTotals();
}

document.getElementById('bid-amount').addEventListener('input', updateBidTotals);

function pickSeat(el, secName, rowLabel, num, price){
  document.querySelectorAll('.dot-picked').forEach(s => { s.classList.remove('dot-picked'); s.classList.add('dot-open'); });
  el.classList.remove('dot-open'); el.classList.add('dot-picked');
  pickedSeat = {secName, rowLabel, num, minBid: price};
  document.getElementById('bid-note').classList.remove('show');
  fillCart(secName, rowLabel, num, price, price);

  document.getElementById('fan-fields').classList.add('show');
  const btn = document.getElementById('claimbtn');
  btn.disabled = false;
  btn.textContent = 'Place bid';
}

function showToast(msg, isError){
  const t = document.getElementById('toast');
  document.getElementById('toast-text').textContent = msg;
  t.querySelector('.dot').style.background = isError ? 'var(--bad, #d33)' : 'var(--good)';
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 3800);
}

let pollTimer = null;

function setRequestStatus(kind, html){
  const el = document.getElementById('request-status');
  el.className = 'show ' + kind;
  el.innerHTML = html;
}

function pollRequestStatus(requestId){
  if (pollTimer) clearInterval(pollTimer);
  const startedAt = Date.now();
  // Matches the server's real offer window (NFL_OFFER_WINDOW_MINUTES) —
  // the server is the source of truth for expiry (via lazy expiry checks
  // on read), this is just a client-side backstop in case a poll response
  // is somehow missed.
  const SELLER_WINDOW_MS = 30 * 60 * 1000;
  const SLOW_POLL_AFTER_MS = 2 * 60 * 1000;

  pollTimer = setInterval(async () => {
    const btn = document.getElementById('claimbtn');
    const elapsed = Date.now() - startedAt;
    if (elapsed > SELLER_WINDOW_MS) {
      clearInterval(pollTimer);
      setRequestStatus('declined', `This offer expired without a response. Try another seat.`);
      btn.textContent = 'Pick another seat';
      return;
    }
    try {
      const res = await fetch(`${API_BASE}/api/nfl/requests/${requestId}`);
      const data = await res.json();
      if (!res.ok) return;
      if (data.status === 'requested') {
        const endsAt = new Date(data.auction_ends_at).getTime();
        const secsLeft = Math.max(0, Math.round((endsAt - Date.now()) / 1000));
        setRequestStatus('pending', `<span class="spin"></span>You're the highest bidder ($${Math.round(data.price)}) — bidding closes in ${secsLeft}s. Someone can still outbid you until then.`);
      } else if (data.status === 'seller_pinged') {
        setRequestStatus('pending', `<span class="spin"></span>Bidding closed at $${Math.round(data.price)} — texting the seat owner for Section ${data.section}, Row ${data.row} Seat ${data.seat}.`);
      } else if (data.status === 'confirmed') {
        clearInterval(pollTimer);
        setRequestStatus('confirmed', `You're in! Gate pass code: <span class="code">${data.pass_code}</span>`);
        btn.textContent = 'Seat claimed';
      } else if (data.status === 'declined') {
        clearInterval(pollTimer);
        setRequestStatus('declined', `The seat owner kept their seat. Try another seat.`);
        btn.textContent = 'Pick another seat';
      } else if (data.status === 'outbid') {
        clearInterval(pollTimer);
        setRequestStatus('declined', `You were outbid — someone bid higher before the window closed. Your card hold was released, no charge was made.`);
        btn.textContent = 'Pick another seat';
      } else if (data.status === 'expired') {
        clearInterval(pollTimer);
        setRequestStatus('declined', `This offer expired without a response. Try another seat.`);
        btn.textContent = 'Pick another seat';
      }
    } catch (err) {
      // transient network hiccup — keep polling
    }
  }, 4000);
}

document.getElementById('claimbtn').addEventListener('click', async () => {
  if (!pickedSeat) return;
  const fanName = document.getElementById('fan-name').value.trim();
  const fanPhone = document.getElementById('fan-phone').value.trim();
  if (!fanName || !fanPhone) {
    showToast('Enter your name and phone number to claim this seat.', true);
    return;
  }
  const bidAmount = updateBidTotals();
  if (bidAmount < pickedSeat.minBid) {
    showToast(`Your bid must be at least $${pickedSeat.minBid}.`, true);
    return;
  }

  const bidNote = document.getElementById('bid-note');
  bidNote.classList.remove('show');
  const btn = document.getElementById('claimbtn');
  btn.disabled = true;
  btn.textContent = 'Sending…';

  try {
    const returnToUrl = window.location.href.split('?')[0];
    const res = await fetch(`${API_BASE}/api/nfl/${TEAM_SLUG}/request`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        section: pickedSeat.secName,
        row: pickedSeat.rowLabel,
        seat: pickedSeat.num,
        price: bidAmount,
        fan_name: fanName,
        fan_phone: fanPhone,
        return_to_url: returnToUrl,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      if (data.current_bid != null) {
        const suggested = Math.ceil(data.current_bid) + 1;
        document.getElementById('bid-amount').value = suggested;
        updateBidTotals();
        bidNote.textContent = data.error;
        bidNote.classList.add('show');
        btn.disabled = false;
        btn.textContent = 'Place bid';
        return;
      }
      throw new Error(data.error || 'Request failed');
    }
    btn.textContent = 'Redirecting to payment…';
    window.location.href = data.checkout_url;
  } catch (err) {
    showToast(`Couldn't send request — is the API running at ${API_BASE}?`, true);
    btn.disabled = false;
    btn.textContent = 'Place bid';
  }
});

async function resumeFromCheckoutReturn(){
  const params = new URLSearchParams(window.location.search);
  const requestId = params.get('request_id');
  const cancelledId = params.get('cancelled_request_id');
  if (!requestId && !cancelledId) return;

  history.replaceState(null, '', window.location.pathname);

  if (cancelledId) {
    showToast('Payment was cancelled — nothing was charged, feel free to try again.', true);
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/nfl/requests/${requestId}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'not found');
    fillCart(data.section, data.row, data.seat, data.price, data.price);
    document.getElementById('claimbtn').textContent = 'Bidding…';
    document.getElementById('claimbtn').disabled = true;
    setRequestStatus('pending', `<span class="spin"></span>Payment authorized — your bid is open to being outbid for a short window.`);
    pollRequestStatus(requestId);
  } catch (err) {
    showToast(`Couldn't load your request — is the API running at ${API_BASE}?`, true);
  }
}

setVB(FULL_VB);
resumeFromCheckoutReturn();
</script>
"""

    HTML = (HTML
            .replace("__FULL_JSON__", FULL_JSON)
            .replace("__META_JSON__", META_JSON)
            .replace("__FULL_VB_JSON__", FULL_VB_JSON)
            .replace("__VIEWBOX_ATTR__", VIEWBOX_ATTR)
            .replace("__ARENA_INNER__", ARENA_INNER)
            .replace("__ARENA_NAME_JSON__", json.dumps(arena_name))
            .replace("__TEAM_SLUG_JSON__", json.dumps(slug))
            .replace("__API_BASE_JSON__", json.dumps("https://fanxp-api.onrender.com"))
            .replace("__TEAM_COLOR_DARK__", team_color_dark)
            .replace("__TEAM_COLOR__", team_color)
            .replace("__TEAM_NAME__", team_name)
            .replace("__ARENA_NAME__", arena_name)
            .replace("__CITY__", city)
            .replace("__SUBLINE__", subline)
            .replace("__CART_NOTE__", cart_note)
            .replace("__LEGEND_DOT_LABEL__", legend_dot_label))

    os.makedirs("../docs", exist_ok=True)
    out_path = f"../docs/nfl_{slug}_seatmap.html"
    with open(out_path, "w") as f:
        f.write(HTML)

    print(f"[{slug}] -> {out_path} ({os.path.getsize(out_path)/1024/1024:.2f} MB)")


if __name__ == "__main__":
    main()
