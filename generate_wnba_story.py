"""
generate_wnba_story.py

Reads all WNBA game data for a team and generates a self-contained HTML page.

Usage:
    python generate_wnba_story.py <team_slug>
    python generate_wnba_story.py all

Output:
    docs/wnba_{slug}_story.html
"""

import csv, json, math, os, sys
from collections import defaultdict
from datetime import datetime

from wnba_teams import WNBA_TEAMS
import supabase_client

WNBA_COLORS = {
    "dream":     "#C8102E",
    "sky":       "#418FDE",
    "sun":       "#F05023",
    "wings":     "#002B5C",
    "valkyries": "#1D1160",
    "fever":     "#FFCD00",
    "aces":      "#000000",
    "sparks":    "#702F8A",
    "lynx":      "#236192",
    "liberty":   "#86CEBC",
    "mercury":   "#CB6015",
    "storm":     "#2C5234",
    "mystics":   "#002B5C",
    "fire":      "#D22630",
    "tempo":     "#CE1126",
}

WNBA_ARENAS = {
    "dream":     "Gateway Center Arena",
    "sky":       "Wintrust Arena",
    "sun":       "Mohegan Sun Arena",
    "wings":     "College Park Center",
    "valkyries": "Chase Center",
    "fever":     "Gainbridge Fieldhouse",
    "aces":      "Michelob ULTRA Arena",
    "sparks":    "Crypto.com Arena",
    "lynx":      "Target Center",
    "liberty":   "Barclays Center",
    "mercury":   "Footprint Center",
    "storm":     "Climate Pledge Arena",
    "mystics":   "Capital One Arena",
    "fire":      "Moda Center",
    "tempo":     "Scotiabank Arena",
}

WNBA_CITIES = {
    "dream":     "Atlanta",
    "sky":       "Chicago",
    "sun":       "Uncasville",
    "wings":     "Arlington",
    "valkyries": "San Francisco",
    "fever":     "Indianapolis",
    "aces":      "Las Vegas",
    "sparks":    "Los Angeles",
    "lynx":      "Minneapolis",
    "liberty":   "New York",
    "mercury":   "Phoenix",
    "storm":     "Seattle",
    "mystics":   "Washington",
    "fire":      "Portland",
    "tempo":     "Toronto",
}


# ── Arena heatmap helpers ──────────────────────────────────────────────────────

def _rate_color(r):
    r = max(0.0, min(1.0, r))
    if r <= 0.5:
        t = r * 2
        return f"#{int(t*255):02x}{int(229-t*14):02x}{int(160-t*160):02x}"
    else:
        t = (r - 0.5) * 2
        return f"#ff{int(215-t*138):02x}{int(t*109):02x}"

def _arc(cx, cy, ia, ib, oa, ob, sd, ed, gap=0.8):
    s, e = sd - gap, ed + gap
    sr, er = math.radians(s), math.radians(e)
    def pt(a, b, ang): return cx + a * math.cos(ang), cy + b * math.sin(ang)
    ix1, iy1 = pt(ia, ib, sr); ix2, iy2 = pt(ia, ib, er)
    ox1, oy1 = pt(oa, ob, sr); ox2, oy2 = pt(oa, ob, er)
    laf = 1 if abs(s - e) > 180 else 0
    return (f"M{ix1:.1f},{iy1:.1f} A{ia},{ib} 0 {laf},1 {ix2:.1f},{iy2:.1f}"
            f" L{ox2:.1f},{oy2:.1f} A{oa},{ob} 0 {laf},0 {ox1:.1f},{oy1:.1f}Z")

def _mid_pt(cx, cy, ia, ib, oa, ob, sd, ed):
    a = math.radians((sd + ed) / 2)
    return cx + (ia + oa) / 2 * math.cos(a), cy + (ib + ob) / 2 * math.sin(a)

def _court(cx, cy):
    hw, hh = 105, 56
    L, R, T, B = cx - hw, cx + hw, cy - hh, cy + hh
    pD, pH, tp, fr = 41, 18, 52, 13
    tpy = math.sqrt(max(0, tp**2 - pD**2))
    aT, aB = cy - tpy, cy + tpy
    fxL, fxR = L + pD, R - pD
    o = []
    o.append(f'<rect x="{L}" y="{T}" width="{hw*2}" height="{hh*2}" fill="#c8922a" rx="3"/>')
    for xl, xr in [(L, L+pD), (R-pD, R)]:
        o.append(f'<rect x="{xl}" y="{cy-pH}" width="{xr-xl}" height="{pH*2}" fill="#b8821f" stroke="rgba(255,255,255,.6)" stroke-width="1"/>')
    o.append(f'<line x1="{cx}" y1="{T}" x2="{cx}" y2="{B}" stroke="rgba(255,255,255,.7)" stroke-width="1.2"/>')
    o.append(f'<circle cx="{cx}" cy="{cy}" r="13" fill="none" stroke="rgba(255,255,255,.7)" stroke-width="1.2"/>')
    for fx, s1, s2 in [(fxL, "0,1", "0,0"), (fxR, "0,0", "0,1")]:
        o.append(f'<path d="M{fx},{cy-fr} A{fr},{fr} 0 {s1} {fx},{cy+fr}" fill="none" stroke="rgba(255,255,255,.65)" stroke-width="1"/>')
        o.append(f'<path d="M{fx},{cy-fr} A{fr},{fr} 0 {s2} {fx},{cy+fr}" fill="none" stroke="rgba(255,255,255,.3)" stroke-width="1" stroke-dasharray="3,3"/>')
    for side, sw in [(L, "1,1"), (R, "1,0")]:
        o.append(f'<line x1="{side}" y1="{T}" x2="{side}" y2="{aT:.1f}" stroke="rgba(255,255,255,.7)" stroke-width="1.2"/>')
        o.append(f'<path d="M{side},{aT:.1f} A{tp},{tp} 0 {sw} {side},{aB:.1f}" fill="none" stroke="rgba(255,255,255,.7)" stroke-width="1.2"/>')
        o.append(f'<line x1="{side}" y1="{aB:.1f}" x2="{side}" y2="{B}" stroke="rgba(255,255,255,.7)" stroke-width="1.2"/>')
    for bx in (L+11, R-11):
        o.append(f'<circle cx="{bx}" cy="{cy}" r="8" fill="none" stroke="rgba(255,255,255,.45)" stroke-width="1"/>')
        o.append(f'<circle cx="{bx}" cy="{cy}" r="4.5" fill="#e87722" stroke="rgba(255,255,255,.8)" stroke-width="1"/>')
    o.append(f'<rect x="{L}" y="{T}" width="{hw*2}" height="{hh*2}" fill="none" stroke="rgba(255,255,255,.8)" stroke-width="1.5" rx="3"/>')
    return "\n".join(o)

def _arena_svg(sec_totals):
    def sec_num(s):
        d = ''.join(filter(str.isdigit, s))
        return int(d) if d else 0

    lower = sorted([s for s in sec_totals if 100 <= sec_num(s) < 200], key=sec_num)
    upper = sorted([s for s in sec_totals if sec_num(s) >= 200], key=sec_num)

    cx, cy = 450, 310
    BG = "#07090f"
    OU_A, OU_B = 282, 194
    IU_A, IU_B = 214, 147
    OL_A, OL_B = 206, 142
    IL_A, IL_B = 128, 80

    o = [f'<rect width="900" height="620" fill="{BG}"/>',
         f'<ellipse cx="{cx}" cy="{cy}" rx="{OU_A+6}" ry="{OU_B+6}" fill="#10182a"/>']

    def ring(secs, ia, ib, oa, ob, gap, label_inner):
        if not secs:
            return
        n = len(secs); sp = 360 / n
        for i, sec in enumerate(secs):
            sd = 180 - i * sp; ed = 180 - (i+1) * sp
            d = sec_totals.get(sec, {}); pre = d.get("pre", 0); ns = d.get("ns", 0)
            rate = ns / pre if pre else 0
            color = _rate_color(rate) if pre > 0 else "#1e293b"
            r_str = f"{rate*100:.1f}%" if pre > 0 else "N/A"
            v_str = f"${d.get('value', 0):,.0f}" if pre > 0 else "N/A"
            attrs = f'data-s="{sec}" data-pre="{pre}" data-ns="{ns}" data-r="{r_str}" data-v="{v_str}"'
            o.append(f'<path d="{_arc(cx,cy,ia,ib,oa,ob,sd,ed,gap)}" fill="{color}" stroke="{BG}" stroke-width="1.2" {attrs} class="sec"/>')
            if label_inner:
                lx, ly = _mid_pt(cx, cy, ia, ib, oa, ob, sd, ed)
                o.append(f'<text x="{lx:.0f}" y="{ly:.0f}" text-anchor="middle" dominant-baseline="middle" font-size="9" font-family="Inter,sans-serif" font-weight="700" fill="rgba(255,255,255,0.92)" pointer-events="none">{sec}</text>')

    ring(upper, IU_A, IU_B, OU_A, OU_B, 0.5, False)
    o.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{IU_A}" ry="{IU_B}" fill="#0c1422"/>')
    o.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{OL_A}" ry="{OL_B}" fill="#10182a"/>')
    ring(lower, IL_A, IL_B, OL_A, OL_B, 0.8, True)
    o.append(f'<ellipse cx="{cx}" cy="{cy}" rx="{IL_A}" ry="{IL_B}" fill="#0c1828"/>')
    o.append(_court(cx, cy))
    return "\n".join(o)


def find_all_games(slug: str) -> list[str]:
    team_dir = f"data/wnba/{slug}"
    if not os.path.isdir(team_dir):
        return []
    games = []
    for folder in sorted(os.listdir(team_dir)):
        gdir = os.path.join(team_dir, folder)
        if os.path.isdir(gdir) and os.path.isfile(f"{gdir}/pre_game.csv"):
            games.append(folder)
    return games


def load_csv_rows(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_game(slug: str, folder: str) -> dict | None:
    gdir = f"data/wnba/{slug}/{folder}"
    meta_path = f"{gdir}/game_meta.json"
    if not os.path.isfile(meta_path):
        return None
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    pre = load_csv_rows(f"{gdir}/pre_game.csv")
    mid = load_csv_rows(f"{gdir}/halftime.csv")
    ns  = load_csv_rows(f"{gdir}/no_shows.csv")
    return {"folder": folder, "meta": meta, "pre": pre, "mid": mid, "noshows": ns}


CONCESSION_PER_SEAT = 35

def load_games_from_supabase(slug: str) -> list[dict]:
    """Load all game data for a team from Supabase (primary data source)."""
    games_meta = supabase_client.fetch_games_for_team(slug, league="wnba")
    games = []
    for g in games_meta:
        game_id   = g.get("id")
        pre_count = supabase_client.count_listings(game_id, "pre_game")
        if pre_count == 0:
            continue
        mid_count = supabase_client.count_listings(game_id, "halftime")
        no_shows  = supabase_client.fetch_no_shows_for_game(game_id)
        ns_value  = sum(float(r.get("price_usd") or 0) for r in no_shows)
        ns_count  = len(no_shows)
        phantom   = ns_value + ns_count * CONCESSION_PER_SEAT
        games.append({
            "folder":     g.get("game_date", ""),
            "meta": {
                "game_date":   g.get("game_date", ""),
                "opponent":    g.get("opponent", ""),
                "arena":       g.get("arena", ""),
                "city":        g.get("city", ""),
                "home_team":   g.get("home_team", ""),
                "league":      g.get("league", "wnba"),
                "day_of_week": g.get("day_of_week", ""),
            },
            "pre":       [],
            "mid":       [],
            "noshows":   no_shows,
            "pre_count": pre_count,
            "mid_count": mid_count,
            "phantom":   phantom,
        })
    return games


def analyse_games(games: list[dict]) -> dict:
    per_game   = []
    sec_totals = defaultdict(lambda: {"pre": 0, "ns": 0, "value": 0.0})

    for g in games:
        pre  = g["pre"]
        mid  = g["mid"]
        ns   = g["noshows"]
        meta = g["meta"]

        pre_count = g["pre_count"] if "pre_count" in g else len(pre)
        mid_count = g["mid_count"] if "mid_count" in g else len(mid)
        ns_count  = len(ns)

        ns_value = sum(float(r.get("price_usd", 0) or 0) for r in ns)
        noshow_rate = ns_count / pre_count if pre_count else 0

        for r in ns:
            sec = r.get("section", "Unknown").strip()
            sec_totals[sec]["ns"] += 1
            try:
                sec_totals[sec]["value"] += float(r.get("price_usd", 0) or 0)
            except (ValueError, TypeError):
                pass
        for r in pre:
            sec = r.get("section", "Unknown").strip()
            sec_totals[sec]["pre"] += 1

        phantom = g.get("phantom", ns_value + ns_count * CONCESSION_PER_SEAT)
        per_game.append({
            "folder":      g["folder"],
            "date":        meta.get("game_date", ""),
            "opponent":    meta.get("opponent", ""),
            "venue":       meta.get("arena", ""),
            "pre_count":   pre_count,
            "mid_count":   mid_count,
            "ns_count":    ns_count,
            "ns_value":    ns_value,
            "phantom":     phantom,
            "noshow_rate": noshow_rate,
            "has_mid":     mid_count > 0,
        })

    games_with_mid = [g for g in per_game if g["has_mid"]]
    avg_rate  = sum(g["noshow_rate"] for g in games_with_mid) / len(games_with_mid) if games_with_mid else 0
    avg_value = sum(g["ns_value"] for g in games_with_mid) / len(games_with_mid) if games_with_mid else 0
    total_ns  = sum(g["ns_count"] for g in per_game)

    top_sections = sorted(
        [{"section": k, **v} for k, v in sec_totals.items()],
        key=lambda x: x["ns"], reverse=True
    )[:10]

    return {
        "per_game":     per_game,
        "avg_rate":     avg_rate,
        "avg_value":    avg_value,
        "total_ns":     total_ns,
        "top_sections": top_sections,
        "sec_totals":   dict(sec_totals),
    }


def fmt_name(slug: str) -> str:
    names = {
        "dream": "Atlanta Dream", "sky": "Chicago Sky", "sun": "Connecticut Sun",
        "wings": "Dallas Wings", "valkyries": "Golden State Valkyries",
        "fever": "Indiana Fever", "aces": "Las Vegas Aces", "sparks": "LA Sparks",
        "lynx": "Minnesota Lynx", "liberty": "New York Liberty",
        "mercury": "Phoenix Mercury", "storm": "Seattle Storm",
        "mystics": "Washington Mystics", "fire": "Portland Fire",
        "tempo": "Toronto Tempo",
    }
    return names.get(slug, slug.replace("_", " ").title())


def generate_html(slug: str) -> str:
    # Supabase is the primary source; fall back to local CSVs for dev/offline use
    games = load_games_from_supabase(slug)
    if not games:
        folders = find_all_games(slug)
        games   = [g for f in folders if (g := load_game(slug, f))]
    has_data = len(games) > 0
    stats    = analyse_games(games) if has_data else {
        "per_game": [], "avg_rate": 0, "avg_value": 0, "total_ns": 0, "top_sections": []
    }

    color    = WNBA_COLORS.get(slug, "#00f5ff")
    arena    = WNBA_ARENAS.get(slug, "")
    city     = WNBA_CITIES.get(slug, "")
    name     = fmt_name(slug)
    per_game = sorted(stats["per_game"], key=lambda g: g["date"], reverse=True)
    arena_svg_str = _arena_svg(stats.get("sec_totals", {})) if has_data else ""

    total_phantom = sum(g["phantom"] for g in per_game if g["has_mid"])
    best_game     = max((g for g in per_game if g["has_mid"] and g["phantom"] > 0), key=lambda g: g["phantom"], default=None)

    rows_html = ""
    for g in per_game:
        rate_str    = f"{g['noshow_rate']*100:.0f}%" if g["has_mid"] else "—"
        phantom_str = f"${g['phantom']:,.0f}" if g["has_mid"] else "—"
        ns_str      = str(g["ns_count"]) if g["has_mid"] else "—"
        date_fmt    = g["date"]
        try:
            date_fmt = datetime.strptime(g["date"], "%Y-%m-%d").strftime("%b %-d, %Y")
        except Exception:
            pass
        opp = g["opponent"] if g["opponent"] else "—"
        rows_html += f"""
        <tr>
          <td>{date_fmt}</td>
          <td>vs {opp}</td>
          <td>{g['pre_count']:,}</td>
          <td>{g['mid_count'] if g['has_mid'] else '—'}</td>
          <td class="hl">{ns_str}</td>
          <td class="hl">{rate_str}</td>
          <td class="hl">{phantom_str}</td>
        </tr>"""

    sec_html = ""
    for s in stats["top_sections"]:
        rate = s["ns"] / s["pre"] * 100 if s["pre"] else 0
        sec_html += f"""
        <tr>
          <td>{s['section']}</td>
          <td>{s['ns']}</td>
          <td>{rate:.0f}%</td>
          <td>${s['value']:,.0f}</td>
        </tr>"""

    total_games   = len(per_game)
    tracked_games = sum(1 for g in per_game if g["has_mid"])

    if not has_data:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>FanXP — {name}</title>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800;900&display=swap" rel="stylesheet" />
  <style>
    body {{ font-family: 'Inter', sans-serif; background: #04060f; color: #eef2ff; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; margin: 0; text-align: center; padding: 40px; }}
    .back {{ font-size: 13px; color: rgba(220,228,255,.5); position: absolute; top: 32px; left: 32px; text-decoration: none; color: rgba(220,228,255,.5); }}
    .emoji {{ font-size: 64px; margin-bottom: 20px; }}
    h1 {{ font-size: 28px; font-weight: 900; margin-bottom: 8px; }}
    p {{ color: rgba(220,228,255,.5); font-size: 15px; max-width: 340px; }}
    .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: {color}; margin-right: 8px; animation: pulse 1.4s infinite; }}
    @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.3}} }}
    .status {{ margin-top: 28px; font-size: 13px; font-weight: 600; color: {color}; }}
  </style>
</head>
<body>
  <a class="back" href="wnba.html">← All WNBA Teams</a>
  <div class="emoji">🏀</div>
  <h1>{name}</h1>
  <p>{arena} · {city}</p>
  <p style="margin-top:16px">No game data yet. The runner checks for home games daily and will populate this page automatically.</p>
  <div class="status"><span class="dot"></span>Monitoring for next home game</div>
</body>
</html>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>FanXP — {name} Seat Intelligence</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet" />
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg:     #04060f;
      --card:   #0b1221;
      --border: rgba(255,255,255,0.08);
      --accent: {color};
      --white:  #eef2ff;
      --muted:  rgba(220,228,255,0.5);
      --font:   'Inter', sans-serif;
    }}
    body {{ font-family: var(--font); background: var(--bg); color: var(--white); line-height: 1.6; padding: 40px 20px; max-width: 1100px; margin: 0 auto; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .back {{ font-size: 13px; color: var(--muted); margin-bottom: 32px; display: block; }}
    .hero {{ margin-bottom: 40px; }}
    .hero-tag {{ font-size: 12px; font-weight: 700; letter-spacing: 2px; text-transform: uppercase; color: var(--accent); margin-bottom: 8px; }}
    .hero h1 {{ font-size: clamp(28px, 5vw, 48px); font-weight: 900; margin-bottom: 6px; }}
    .hero .sub {{ color: var(--muted); font-size: 15px; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 40px; }}
    .kpi {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 20px; }}
    .kpi-val {{ font-size: 32px; font-weight: 900; color: var(--accent); }}
    .kpi-label {{ font-size: 12px; color: var(--muted); margin-top: 4px; text-transform: uppercase; letter-spacing: 0.5px; }}
    .section {{ margin-bottom: 40px; }}
    .section h2 {{ font-size: 18px; font-weight: 700; margin-bottom: 16px; border-left: 3px solid var(--accent); padding-left: 12px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th {{ text-align: left; padding: 10px 12px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); border-bottom: 1px solid var(--border); }}
    td {{ padding: 12px; border-bottom: 1px solid var(--border); color: var(--white); }}
    tr:last-child td {{ border-bottom: none; }}
    td.hl {{ color: var(--accent); font-weight: 700; }}
    .tbl-wrap {{ background: var(--card); border: 1px solid var(--border); border-radius: 14px; overflow: hidden; overflow-x: auto; }}
    .footer {{ margin-top: 60px; font-size: 12px; color: var(--muted); text-align: center; }}
    .phantom-alert {{ background: linear-gradient(135deg, rgba(255,255,255,.04), rgba(255,255,255,.01)); border: 1px solid rgba(255,255,255,.12); border-left: 3px solid var(--accent); border-radius: 12px; padding: 16px 20px; margin-bottom: 40px; font-size: 14px; color: var(--muted); line-height: 1.6; }}
    .phantom-alert strong {{ color: var(--white); }}
    .arena-wrap {{ background: #07090f; border: 1px solid var(--border); border-radius: 14px; overflow: hidden; margin-bottom: 40px; }}
    .arena-wrap svg {{ width: 100%; height: auto; display: block; }}
    .sec {{ cursor: pointer; transition: filter .08s; }}
    .sec:hover, .sec.on {{ filter: brightness(1.3) drop-shadow(0 0 4px rgba(255,255,255,.3)); stroke: #fff !important; stroke-width: 2px !important; }}
    .arena-legend {{ display: flex; align-items: center; gap: 16px; padding: 12px 20px; border-top: 1px solid var(--border); font-size: 12px; color: var(--muted); }}
    .arena-legend .lb {{ width: 120px; height: 8px; border-radius: 4px; background: linear-gradient(90deg,#16a34a,#d97706,#dc2626); }}
    .arena-legend .lnd {{ width: 18px; height: 8px; border-radius: 3px; background: #1e293b; }}
    #tt {{ position: fixed; z-index: 99; pointer-events: none; display: none; min-width: 180px;
      background: rgba(3,6,16,.97); border: 1px solid rgba(0,245,255,.15); border-radius: 12px;
      padding: 12px 15px; backdrop-filter: blur(20px); box-shadow: 0 12px 40px rgba(0,0,0,.8); }}
    .th {{ display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }}
    .tn {{ font-size: .78rem; font-weight: 800; color: var(--accent); }}
    .tv {{ font-size: .95rem; font-weight: 900; }}
    hr.td {{ border: none; border-top: 1px solid rgba(255,255,255,.06); margin: 0 0 7px; }}
    .tr {{ display: flex; justify-content: space-between; margin-bottom: 2px; }}
    .tk {{ font-size: .67rem; color: var(--muted); }}
    .tw {{ font-size: .73rem; font-weight: 600; }}
  </style>
</head>
<body>
  <a class="back" href="wnba.html">← All WNBA Teams</a>

  <div class="hero">
    <div class="hero-tag">WNBA · {city} · {arena}</div>
    <h1>{name} Seat Intelligence</h1>
    <p class="sub">Secondary-market no-show tracking: pre-game vs. halftime listings</p>
  </div>

  <div class="kpi-grid">
    <div class="kpi"><div class="kpi-val">{total_games}</div><div class="kpi-label">Games tracked</div></div>
    <div class="kpi"><div class="kpi-val">{stats['total_ns']:,}</div><div class="kpi-label">Total no-show seats</div></div>
    <div class="kpi"><div class="kpi-val">{stats['avg_rate']*100:.0f}%</div><div class="kpi-label">Avg no-show rate</div></div>
    <div class="kpi" style="border-color:rgba(255,255,255,.18)"><div class="kpi-val" style="color:{color}">${total_phantom:,.0f}</div><div class="kpi-label">Total phantom revenue</div></div>
    <div class="kpi" style="border-color:rgba(255,255,255,.18)"><div class="kpi-val" style="color:{color}">${stats['avg_value'] + 35 * (stats['total_ns'] / max(tracked_games, 1)):,.0f}</div><div class="kpi-label">Avg phantom / game</div></div>
  </div>
  {'<div class="phantom-alert">Your arena lost <strong>$' + f"{best_game['phantom']:,.0f}" + '</strong> in phantom revenue — <strong>' + str(best_game['ns_count']) + ' no-shows × seat price + $35 concession spend</strong> — during the ' + (datetime.strptime(best_game["date"], "%Y-%m-%d").strftime("%b %-d") if best_game else "") + ' game vs ' + (best_game["opponent"] if best_game else "") + '.</div>' if best_game else ""}

  <div class="arena-wrap">
    <svg viewBox="0 0 900 620" xmlns="http://www.w3.org/2000/svg">{arena_svg_str}</svg>
    <div class="arena-legend">
      <span>0%</span><div class="lb"></div><span>100% no-show</span>
      &nbsp;&nbsp;<div class="lnd"></div><span>No data</span>
    </div>
  </div>

  <div class="section">
    <h2>Game Log</h2>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr><th>Date</th><th>Opponent</th><th>Pre-game</th><th>Halftime</th><th>No-shows</th><th>Rate</th><th>Phantom Revenue</th></tr>
        </thead>
        <tbody>{rows_html if rows_html else '<tr><td colspan="7" style="color:var(--muted);text-align:center;padding:24px">No games yet</td></tr>'}</tbody>
      </table>
    </div>
  </div>

  <div class="section">
    <h2>Top No-Show Sections</h2>
    <div class="tbl-wrap">
      <table>
        <thead><tr><th>Section</th><th>No-show seats</th><th>Rate</th><th>Total value</th></tr></thead>
        <tbody>{sec_html if sec_html else '<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:24px">No data yet</td></tr>'}</tbody>
      </table>
    </div>
  </div>

  <div class="footer">Generated by FanXP · {datetime.now().strftime("%b %-d, %Y")}</div>
</body>

<div id="tt">
  <div class="th"><span class="tn" id="tn2">—</span><span class="tv" id="tv2">—</span></div>
  <hr class="td"/>
  <div class="tr"><span class="tk">Pre-game</span><span class="tw" id="tpre">—</span></div>
  <div class="tr"><span class="tk">No-shows</span><span class="tw" id="tns">—</span></div>
  <div class="tr"><span class="tk">Value</span><span class="tw" id="tdv">—</span></div>
</div>
<script>
(function(){{
  var tt=document.getElementById('tt'),hi=null;
  document.addEventListener('mousemove',function(e){{
    var el=e.target,ok=el&&el.classList.contains('sec');
    if(ok){{
      var W=tt.offsetWidth||190,H=tt.offsetHeight||120;
      tt.style.left=(e.clientX+14+W>innerWidth?e.clientX-W-8:e.clientX+14)+'px';
      tt.style.top=(e.clientY-12+H>innerHeight?e.clientY-H-4:e.clientY-12)+'px';
      if(el!==hi){{
        if(hi)hi.classList.remove('on');
        hi=el;hi.classList.add('on');
        var s=el.dataset.s,r=el.dataset.r,v=el.dataset.v;
        document.getElementById('tn2').textContent='Section '+s;
        document.getElementById('tv2').textContent=r;
        var rv=parseFloat(r)/100;
        document.getElementById('tv2').style.color=rv<.33?'#22c55e':rv<.66?'#f59e0b':'#ef4444';
        document.getElementById('tpre').textContent=parseInt(el.dataset.pre).toLocaleString();
        document.getElementById('tns').textContent=parseInt(el.dataset.ns).toLocaleString();
        document.getElementById('tdv').textContent=v;
      }}
      tt.style.display='block';
    }} else {{
      if(hi){{hi.classList.remove('on');hi=null;}}
      tt.style.display='none';
    }}
  }});
}})();
</script>
</html>"""


def main():
    os.makedirs("docs", exist_ok=True)
    slugs = list(WNBA_TEAMS.keys()) if len(sys.argv) < 2 or sys.argv[1] == "all" else [sys.argv[1].lower()]
    for slug in slugs:
        html = generate_html(slug)
        out  = f"docs/wnba_{slug}_story.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [{slug}] → {out}")


if __name__ == "__main__":
    main()
