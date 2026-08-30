"""
generate_mlb_story.py

Reads all MLB game data for a team and generates a self-contained HTML page.

Usage:
    python generate_mlb_story.py <team_slug>
    python generate_mlb_story.py all

Output:
    docs/mlb_{slug}_story.html
"""

import csv, json, os, sys
from collections import defaultdict
from datetime import datetime

from mlb_teams import MLB_TEAMS
import supabase_client

MLB_COLORS = {
    "diamondbacks": "#A71930", "braves":      "#CE1141", "orioles":   "#DF4601",
    "redsox":       "#BD3039", "cubs":        "#0E3386", "whitesox":  "#27251F",
    "reds":         "#C6011F", "guardians":   "#00385D", "rockies":   "#33006F",
    "tigers":       "#0C2340", "astros":      "#002D62", "royals":    "#004687",
    "angels":       "#BA0021", "dodgers":     "#005A9C", "marlins":   "#00A3E0",
    "brewers":      "#12284B", "twins":       "#002B5C", "mets":      "#002D72",
    "yankees":      "#003087", "athletics":   "#003831", "phillies":  "#E81828",
    "pirates":      "#27251F", "padres":      "#2F241D", "giants":    "#FD5A1E",
    "mariners":     "#0C2C56", "cardinals":   "#C41E3A", "rays":      "#092C5C",
    "rangers":      "#003278", "bluejays":    "#134A8E", "nationals": "#AB0003",
}

MLB_ARENAS = {
    "diamondbacks": "Chase Field",              "braves":      "Truist Park",
    "orioles":      "Oriole Park at Camden Yards", "redsox":  "Fenway Park",
    "cubs":         "Wrigley Field",            "whitesox":   "Guaranteed Rate Field",
    "reds":         "Great American Ball Park", "guardians":  "Progressive Field",
    "rockies":      "Coors Field",              "tigers":     "Comerica Park",
    "astros":       "Minute Maid Park",         "royals":     "Kauffman Stadium",
    "angels":       "Angel Stadium",            "dodgers":    "Dodger Stadium",
    "marlins":      "loanDepot park",           "brewers":    "American Family Field",
    "twins":        "Target Field",             "mets":       "Citi Field",
    "yankees":      "Yankee Stadium",           "athletics":  "Sutter Health Park",
    "phillies":     "Citizens Bank Park",       "pirates":    "PNC Park",
    "padres":       "Petco Park",               "giants":     "Oracle Park",
    "mariners":     "T-Mobile Park",            "cardinals":  "Busch Stadium",
    "rays":         "Tropicana Field",          "rangers":    "Globe Life Field",
    "bluejays":     "Rogers Centre",            "nationals":  "Nationals Park",
}


def find_all_games(slug: str) -> list[str]:
    team_dir = f"data/mlb/{slug}"
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
    gdir = f"data/mlb/{slug}/{folder}"
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
    games_meta = supabase_client.fetch_games_for_team(slug, league="mlb")
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
                "league":      g.get("league", "mlb"),
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
    # Section-level breakdown is built only from no_shows rows -- the
    # Supabase data path never fetches full pre_game listing rows (only
    # counts, via count_listings), so "share of pre_game listings that
    # went unsold" isn't computable per section without an extra query.
    # "ns" / "value" are real; the top-sections table reports each
    # section's share of total no-shows instead of a resale rate.
    sec_totals = defaultdict(lambda: {"ns": 0, "value": 0.0})

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
            sec_totals[sec]["ns"] += 1
            try:
                sec_totals[sec]["value"] += float(r.get("price_usd", 0) or 0)
            except (ValueError, TypeError):
                pass

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

    color   = MLB_COLORS.get(slug, "#1a73e8")
    arena   = MLB_ARENAS.get(slug, "")
    city    = MLB_TEAMS.get(slug, {}).get("city", "")
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

    # Build top-sections rows. "Rate" here is this section's share of all
    # no-shows across every tracked game (see analyse_games) -- not a
    # per-section resale rate, which isn't computable from the Supabase
    # data path without an extra full-listings query.
    total_ns_all = stats["total_ns"] or 1
    sec_html = ""
    for s in stats["top_sections"]:
        rate = s["ns"] / total_ns_all * 100
        sec_html += f"""
        <tr>
          <td>{s['section']}</td>
          <td>{s['ns']}</td>
          <td>{rate:.0f}%</td>
          <td>${s['value']:,.0f}</td>
        </tr>"""

    total_games   = len(per_game)
    tracked_games = sum(1 for g in per_game if g["has_mid"])

    FONT_LINKS = """<link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Newsreader:ital,opsz,wght@0,6..72,500;0,6..72,600;1,6..72,500&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet" />"""

    if not has_data:
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Fan XP · {name} Dashboard</title>
  {FONT_LINKS}
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: 'Inter', sans-serif; background: #fafaf9; color: #16181c; display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: 100vh; margin: 0; text-align: center; padding: 40px; }}
    a {{ color: {color}; text-decoration: none; }}
    .back {{ font-size: 13px; color: #9a9da3; position: absolute; top: 32px; left: 32px; }}
    .emoji {{ font-size: 56px; margin-bottom: 20px; }}
    h1 {{ font-family: 'Newsreader', serif; font-size: 26px; font-weight: 500; margin-bottom: 8px; }}
    p {{ color: #6d7076; font-size: 15px; max-width: 340px; }}
    .dot {{ display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: {color}; margin-right: 8px; animation: pulse 1.4s infinite; }}
    @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.3}} }}
    .status {{ margin-top: 28px; font-size: 13px; font-weight: 600; color: {color}; }}
  </style>
</head>
<body>
  <a class="back" href="mlb.html">← All MLB Teams</a>
  <div class="emoji">⚾</div>
  <h1>{name}</h1>
  <p>{arena} · {city}</p>
  <p style="margin-top:16px">No game data collected yet. The runner checks for home games automatically and will populate this dashboard once one has been played.</p>
  <div class="status"><span class="dot"></span>Monitoring for next home game</div>
</body>
</html>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Fan XP · {name} Dashboard</title>
  {FONT_LINKS}
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg:      #fafaf9;
      --surface: #ffffff;
      --border:  #e7e6e2;
      --text:    #16181c;
      --muted:   #6d7076;
      --muted-2: #9a9da3;
      --accent:  {color};
      --sans:    'Inter', sans-serif;
      --serif:   'Newsreader', serif;
      --mono:    'IBM Plex Mono', monospace;
      --max:     900px;
    }}
    body {{ font-family: var(--sans); background: var(--bg); color: var(--text); line-height: 1.55; -webkit-font-smoothing: antialiased; }}
    a {{ color: var(--accent); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .wrap {{ max-width: var(--max); margin: 0 auto; padding: 44px 24px 80px; }}
    .back {{ font-size: .82rem; color: var(--muted); margin-bottom: 28px; display: inline-block; }}
    .back:hover {{ color: var(--text); text-decoration: none; }}
    .hero {{ margin-bottom: 36px; }}
    .hero-tag {{ font-size: .72rem; font-weight: 600; letter-spacing: .12em; text-transform: uppercase; color: var(--accent); margin-bottom: 10px; }}
    .hero h1 {{ font-family: var(--serif); font-size: clamp(1.7rem, 3.6vw, 2.3rem); font-weight: 500; letter-spacing: -.005em; margin-bottom: 8px; }}
    .hero .sub {{ color: var(--muted); font-size: .92rem; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 1px; background: var(--border); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; margin-bottom: 32px; }}
    .kpi {{ background: var(--surface); padding: 18px 20px; }}
    .kpi-val {{ font-family: var(--mono); font-size: 1.7rem; font-weight: 600; letter-spacing: -.02em; color: var(--text); }}
    .kpi.phantom .kpi-val {{ color: var(--accent); }}
    .kpi-label {{ font-size: .72rem; color: var(--muted); margin-top: 4px; text-transform: uppercase; letter-spacing: .06em; }}
    .section {{ margin-bottom: 36px; }}
    .section h2 {{ font-size: .82rem; font-weight: 600; letter-spacing: .08em; text-transform: uppercase; margin-bottom: 14px; color: var(--muted); }}
    table {{ width: 100%; border-collapse: collapse; font-size: .84rem; }}
    th {{ text-align: left; padding: 9px 12px; font-size: .68rem; font-weight: 600; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); border-bottom: 1px solid var(--border); white-space: nowrap; }}
    td {{ padding: 10px 12px; border-bottom: 1px solid var(--border); color: var(--text); font-variant-numeric: tabular-nums; }}
    tr:last-child td {{ border-bottom: none; }}
    td.hl {{ color: var(--accent); font-weight: 600; }}
    .tbl-wrap {{ background: var(--surface); border: 1px solid var(--border); border-radius: 10px; overflow: hidden; overflow-x: auto; }}
    .footer {{ margin-top: 48px; font-size: .78rem; color: var(--muted-2); text-align: center; }}
    .phantom-alert {{
      background: #fff8e8; border: 1px solid #f0dfae; border-radius: 8px;
      padding: 14px 16px; margin-bottom: 32px; font-size: .85rem; color: #6b5a1f; line-height: 1.6;
    }}
    .phantom-alert strong {{ color: #4a3d10; }}
  </style>
</head>
<body>
  <div class="wrap">
  <a class="back" href="mlb.html">← All MLB Teams</a>

  <div class="hero">
    <div class="hero-tag">MLB Team Dashboard · {city} · {arena}</div>
    <h1>{name}</h1>
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
    <div class="kpi phantom">
      <div class="kpi-val">${total_phantom:,.0f}</div>
      <div class="kpi-label">Total phantom revenue</div>
    </div>
    <div class="kpi phantom">
      <div class="kpi-val">${stats['avg_value'] + 35 * (stats['total_ns'] / max(tracked_games, 1)):,.0f}</div>
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
          <tr><th>Section</th><th>No-show seats</th><th>Share of no-shows</th><th>Total value</th></tr>
        </thead>
        <tbody>{sec_html if sec_html else '<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:24px">No data yet</td></tr>'}</tbody>
      </table>
    </div>
  </div>

  <div class="footer">
    Generated by Fan XP · {datetime.now().strftime("%b %-d, %Y")}
  </div>
  </div>
</body>
</html>"""
    return html


def main():
    os.makedirs("../docs", exist_ok=True)

    if len(sys.argv) < 2 or sys.argv[1] == "all":
        slugs = list(MLB_TEAMS.keys())
    else:
        slugs = [sys.argv[1].lower()]

    for slug in slugs:
        html = generate_html(slug)
        if not html:
            continue
        out = f"../docs/mlb_{slug}_story.html"
        with open(out, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"  [{slug}] → {out}")


if __name__ == "__main__":
    main()
