"""
generate_wnba_story.py

Reads all WNBA game data for a team and generates a self-contained HTML page.

Usage:
    python generate_wnba_story.py <team_slug>
    python generate_wnba_story.py all

Output:
    docs/wnba_{slug}_story.html
"""

import csv, json, os, sys
from collections import defaultdict
from datetime import datetime

from wnba_teams import WNBA_TEAMS

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


def analyse_games(games: list[dict]) -> dict:
    per_game   = []
    sec_totals = defaultdict(lambda: {"pre": 0, "ns": 0, "value": 0.0})

    for g in games:
        pre  = g["pre"]
        mid  = g["mid"]
        ns   = g["noshows"]
        meta = g["meta"]

        pre_count = len(pre)
        mid_count = len(mid)
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

        per_game.append({
            "folder":      g["folder"],
            "date":        meta.get("game_date", ""),
            "opponent":    meta.get("opponent", ""),
            "venue":       meta.get("arena", ""),
            "pre_count":   pre_count,
            "mid_count":   mid_count,
            "ns_count":    ns_count,
            "ns_value":    ns_value,
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

    rows_html = ""
    for g in per_game:
        rate_str = f"{g['noshow_rate']*100:.0f}%" if g["has_mid"] else "—"
        val_str  = f"${g['ns_value']:,.0f}" if g["has_mid"] else "—"
        ns_str   = str(g["ns_count"]) if g["has_mid"] else "—"
        date_fmt = g["date"]
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
          <td>{val_str}</td>
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
    <div class="kpi"><div class="kpi-val">{tracked_games}</div><div class="kpi-label">With halftime data</div></div>
    <div class="kpi"><div class="kpi-val">{stats['avg_rate']*100:.0f}%</div><div class="kpi-label">Avg no-show rate</div></div>
    <div class="kpi"><div class="kpi-val">${stats['avg_value']:,.0f}</div><div class="kpi-label">Avg value/game</div></div>
    <div class="kpi"><div class="kpi-val">{stats['total_ns']:,}</div><div class="kpi-label">Total no-show seats</div></div>
  </div>

  <div class="section">
    <h2>Game Log</h2>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr><th>Date</th><th>Opponent</th><th>Pre-game</th><th>Halftime</th><th>No-shows</th><th>Rate</th><th>Value</th></tr>
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
