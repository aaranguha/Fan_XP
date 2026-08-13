"""
generate_nfl_story.py

Reads all NFL game data for a team and generates a self-contained HTML page.
Mirrors generate_mlb_story.py structure but for football.

Usage:
    python generate_nfl_story.py <team_slug>
    python generate_nfl_story.py all

Output:
    docs/nfl_{slug}_story.html
"""

import csv, json, os, sys
from collections import defaultdict
from datetime import datetime

from nfl_teams import NFL_TEAMS
import supabase_client

NFL_COLORS = {
    "bills": "#00338D", "dolphins": "#008E97", "patriots": "#002244", "jets": "#125740",
    "ravens": "#241773", "bengals": "#FB4F14", "browns": "#311D00", "steelers": "#FFB612",
    "texans": "#03202F", "colts": "#002C5F", "jaguars": "#101820", "titans": "#0C2340",
    "broncos": "#FB4F14", "chiefs": "#E31837", "raiders": "#000000", "chargers": "#0080C6",
    "cowboys": "#041E42", "giants": "#0B2265", "eagles": "#004C54", "commanders": "#5A1414",
    "bears": "#0B162A", "lions": "#0076B6", "packers": "#203731", "vikings": "#4F2683",
    "falcons": "#A71930", "panthers": "#0085CA", "saints": "#D3BC8D", "buccaneers": "#D50A0A",
    "cardinals": "#97233F", "rams": "#003594", "49ers": "#AA0000", "seahawks": "#002244",
}

NFL_ARENAS = {
    "bills": "Highmark Stadium", "dolphins": "Hard Rock Stadium",
    "patriots": "Gillette Stadium", "jets": "MetLife Stadium",
    "ravens": "M&T Bank Stadium", "bengals": "Paycor Stadium",
    "browns": "Huntington Bank Field", "steelers": "Acrisure Stadium",
    "texans": "NRG Stadium", "colts": "Lucas Oil Stadium",
    "jaguars": "EverBank Stadium", "titans": "Nissan Stadium",
    "broncos": "Empower Field at Mile High", "chiefs": "GEHA Field at Arrowhead Stadium",
    "raiders": "Allegiant Stadium", "chargers": "SoFi Stadium",
    "cowboys": "AT&T Stadium", "giants": "MetLife Stadium",
    "eagles": "Lincoln Financial Field", "commanders": "Commanders Field",
    "bears": "Soldier Field", "lions": "Ford Field",
    "packers": "Lambeau Field", "vikings": "U.S. Bank Stadium",
    "falcons": "Mercedes-Benz Stadium", "panthers": "Bank of America Stadium",
    "saints": "Caesars Superdome", "buccaneers": "Raymond James Stadium",
    "cardinals": "State Farm Stadium", "rams": "SoFi Stadium",
    "49ers": "Levi's Stadium", "seahawks": "Lumen Field",
}


def find_all_games(slug: str) -> list[str]:
    team_dir = f"data/nfl/{slug}"
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
    gdir = f"data/nfl/{slug}/{folder}"
    meta_path = f"{gdir}/game_meta.json"
    if not os.path.isfile(meta_path):
        return None
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    pre  = load_csv_rows(f"{gdir}/pre_game.csv")
    mid  = load_csv_rows(f"{gdir}/mid_game.csv")
    ns   = load_csv_rows(f"{gdir}/no_shows.csv")
    return {"folder": folder, "meta": meta, "pre": pre, "mid": mid, "noshows": ns}


CONCESSION_PER_SEAT = 35

def load_games_from_supabase(slug: str) -> list[dict]:
    """Load all game data for a team from Supabase (primary data source)."""
    games_meta = supabase_client.fetch_games_for_team(slug, league="nfl")
    games = []
    for g in games_meta:
        game_id   = g.get("id")
        pre_count = supabase_client.count_listings(game_id, "pre_game")
        if pre_count == 0:
            continue  # no scrape data yet for this game
        mid_count = supabase_client.count_listings(game_id, "mid_game")
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
                "league":      g.get("league", "nfl"),
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

        # Use explicit counts from Supabase if available, otherwise len()
        pre_count = g["pre_count"] if "pre_count" in g else len(pre)
        mid_count = g["mid_count"] if "mid_count" in g else len(mid)
        ns_count  = len(ns)

        ns_value = 0.0
        for r in ns:
            try:
                ns_value += float(r.get("price_usd", 0) or 0)
            except (ValueError, TypeError):
                pass

        noshow_rate = ns_count / pre_count if pre_count else 0

        for r in ns:
            sec = r.get("section", "Unknown").strip()
            sec_totals[sec]["pre"]   += 1
            sec_totals[sec]["ns"]    += 1
            try:
                sec_totals[sec]["value"] += float(r.get("price_usd", 0) or 0)
            except (ValueError, TypeError):
                pass
        for r in pre:
            sec = r.get("section", "Unknown").strip()
            if r not in ns:
                sec_totals[sec]["pre"] += 1

        phantom = g.get("phantom", ns_value + ns_count * CONCESSION_PER_SEAT)
        per_game.append({
            "folder":       g["folder"],
            "date":         meta.get("game_date", ""),
            "opponent":     meta.get("opponent", ""),
            "venue":        meta.get("arena", ""),
            "pre_count":    pre_count,
            "mid_count":    mid_count,
            "ns_count":     ns_count,
            "ns_value":     ns_value,
            "phantom":      phantom,
            "noshow_rate":  noshow_rate,
            "has_mid":      mid_count > 0,
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
    return slug.replace("_", " ").title()


def generate_html(slug: str) -> str:
    # Supabase is the primary source; fall back to local CSVs for dev/offline use
    games = load_games_from_supabase(slug)
    if not games:
        folders = find_all_games(slug)
        games = [g for f in folders if (g := load_game(slug, f))]
    has_data = len(games) > 0
    stats = analyse_games(games) if has_data else {"per_game": [], "avg_rate": 0, "avg_value": 0, "total_ns": 0, "top_sections": []}

    color   = NFL_COLORS.get(slug, "#1a73e8")
    arena   = NFL_ARENAS.get(slug, "")
    city    = NFL_TEAMS.get(slug, {}).get("city", "")
    name    = fmt_name(slug)
    per_game = sorted(stats["per_game"], key=lambda g: g["date"], reverse=True)

    total_phantom = sum(g["phantom"] for g in per_game if g["has_mid"])
    best_game     = max((g for g in per_game if g["has_mid"] and g["phantom"] > 0), key=lambda g: g["phantom"], default=None)

    # Build per-game rows
    rows_html = ""
    for g in per_game:
        rate_str    = f"{g['noshow_rate']*100:.0f}%" if g["has_mid"] else "—"
        val_str     = f"${g['ns_value']:,.0f}" if g["has_mid"] else "—"
        phantom_str = f"${g['phantom']:,.0f}" if g["has_mid"] else "—"
        ns_str      = str(g["ns_count"]) if g["has_mid"] else "—"
        date_fmt    = g["date"]
        try:
            date_fmt = datetime.strptime(g["date"], "%Y-%m-%d").strftime("%b %-d, %Y")
        except Exception:
            pass
        opp = fmt_name(g["opponent"]) if g["opponent"] else "—"
        rows_html += f"""
        <tr>
          <td>{date_fmt}</td>
          <td>vs {opp}</td>
          <td>{g['pre_count']:,}</td>
          <td>{g['mid_count'] if g['has_mid'] else '—'}</td>
          <td class="hl">{ns_str}</td>
          <td class="hl">{rate_str}</td>
          <td>{val_str}</td>
          <td class="hl">{phantom_str}</td>
        </tr>"""

    # Build top-sections rows
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
    a {{ color: {color}; text-decoration: none; }}
    .back {{ font-size: 13px; color: rgba(220,228,255,.5); position: absolute; top: 32px; left: 32px; }}
    .emoji {{ font-size: 64px; margin-bottom: 20px; }}
    h1 {{ font-size: 28px; font-weight: 900; margin-bottom: 8px; }}
    p {{ color: rgba(220,228,255,.5); font-size: 15px; max-width: 340px; }}
    .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: {color}; margin-right: 8px; animation: pulse 1.4s infinite; }}
    @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.3}} }}
    .status {{ margin-top: 28px; font-size: 13px; font-weight: 600; color: {color}; }}
  </style>
</head>
<body>
  <a class="back" href="nfl.html">← All NFL Teams</a>
  <div class="emoji">🏈</div>
  <h1>{name}</h1>
  <p>{arena} · {city}</p>
  <p style="margin-top:16px">No game data collected yet. The runner checks for home games daily and will populate this page automatically.</p>
  <div class="status"><span class="dot"></span>Monitoring for next home game</div>
</body>
</html>"""

    html = f"""<!DOCTYPE html>
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
    body {{ font-family: var(--font); background: var(--bg); color: var(--white); line-height: 1.6; padding: 40px 20px; }}
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
  </style>
</head>
<body>
  <a class="back" href="/">← Back to FanXP</a>

  <div class="hero">
    <div class="hero-tag">NFL · {city} · {arena}</div>
    <h1>{name} Seat Intelligence</h1>
    <p class="sub">Secondary-market no-show tracking: pre-game vs. mid-game listings</p>
  </div>

  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-val">{total_games}</div>
      <div class="kpi-label">Games tracked</div>
    </div>
    <div class="kpi">
      <div class="kpi-val">{stats['total_ns']:,}</div>
      <div class="kpi-label">Total no-show seats</div>
    </div>
    <div class="kpi">
      <div class="kpi-val">{stats['avg_rate']*100:.0f}%</div>
      <div class="kpi-label">Avg no-show rate</div>
    </div>
    <div class="kpi">
      <div class="kpi-val">${stats['avg_value']:,.0f}</div>
      <div class="kpi-label">Avg seat value / game</div>
    </div>
    <div class="kpi" style="border-color:rgba(255,255,255,.18)">
      <div class="kpi-val" style="color:{color}">${total_phantom:,.0f}</div>
      <div class="kpi-label">Total phantom revenue</div>
    </div>
    <div class="kpi" style="border-color:rgba(255,255,255,.18)">
      <div class="kpi-val" style="color:{color}">${stats['avg_value'] + 35 * (stats['total_ns'] / max(tracked_games, 1)):,.0f}</div>
      <div class="kpi-label">Avg phantom / game</div>
    </div>
  </div>
  {'<div class="phantom-alert">Your stadium lost <strong>$' + f"{best_game['phantom']:,.0f}" + '</strong> in phantom revenue — <strong>' + str(best_game['ns_count']) + ' no-shows × seat price + $35 concession spend</strong> — during the ' + (datetime.strptime(best_game["date"], "%Y-%m-%d").strftime("%b %-d") if best_game else "") + ' game vs ' + (fmt_name(best_game["opponent"]) if best_game else "") + '.</div>' if best_game else ""}

  <div class="section">
    <h2>Game Log</h2>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th>Date</th><th>Opponent</th><th>Pre-game</th><th>Mid-game</th>
            <th>No-shows</th><th>Rate</th><th>Seat value</th><th>Phantom Revenue</th>
          </tr>
        </thead>
        <tbody>{rows_html}</tbody>
      </table>
    </div>
  </div>

  <div class="section">
    <h2>Top No-Show Sections</h2>
    <div class="tbl-wrap">
      <table>
        <thead>
          <tr><th>Section</th><th>No-show seats</th><th>Rate</th><th>Total value</th></tr>
        </thead>
        <tbody>{sec_html if sec_html else '<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:24px">No data yet</td></tr>'}</tbody>
      </table>
    </div>
  </div>

  <div class="footer">
    Generated by FanXP · {datetime.now().strftime("%b %-d, %Y")}
  </div>
</body>
</html>"""
    return html


def main():
    os.makedirs("docs", exist_ok=True)

    if len(sys.argv) < 2 or sys.argv[1] == "all":
        slugs = list(NFL_TEAMS.keys())
    else:
        slugs = [sys.argv[1].lower()]

    for slug in slugs:
        html = generate_html(slug)
        if not html:
            continue
        out = f"docs/nfl_{slug}_story.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [{slug}] → {out}")


if __name__ == "__main__":
    main()
