"""
generate_dashboard.py

Reads all seat-level game data for a team and generates a self-contained
HTML dashboard with analytics and charts.

Usage:
    python generate_dashboard.py <team_slug>
    e.g. python generate_dashboard.py magic

Output:
    Website/dashboard_<team_slug>.html
"""

import csv
import json
import os
import sys
from datetime import datetime

DATA_DIR    = "data"
OUTPUT_DIR  = "Website"


# ── Data loading ───────────────────────────────────────────────────────────────

def load_games(team_slug: str) -> list[dict]:
    team_dir = os.path.join(DATA_DIR, team_slug)
    if not os.path.isdir(team_dir):
        raise ValueError(f"No data folder found for '{team_slug}'")

    games = []
    for folder in sorted(os.listdir(team_dir)):
        gdir = os.path.join(team_dir, folder)
        if not os.path.isdir(gdir):
            continue

        meta_path     = os.path.join(gdir, "game_meta.json")
        pre_path      = os.path.join(gdir, "pre_game.csv")
        ht_path       = os.path.join(gdir, "halftime.csv")
        noshows_path  = os.path.join(gdir, "no_shows.csv")

        # Need at least meta + no_shows to be useful
        if not (os.path.isfile(meta_path) and os.path.isfile(noshows_path)):
            continue

        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)

        def load_csv(path):
            if not os.path.isfile(path):
                return []
            with open(path, newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f))

        pre_rows     = load_csv(pre_path)
        ht_rows      = load_csv(ht_path)
        noshow_rows  = load_csv(noshows_path)

        games.append({
            "folder":    folder,
            "meta":      meta,
            "pre":       pre_rows,
            "halftime":  ht_rows,
            "noshows":   noshow_rows,
        })

    return games


# ── Analytics ──────────────────────────────────────────────────────────────────

def analyse(games: list[dict]) -> dict:
    per_game = []
    section_data = {}   # section → {noshow_seats, pre_seats}
    dow_data     = {}   # day_of_week → {games, total_noshow_pct}
    opp_corr     = []   # [{opp_win_pct, noshow_rate, opponent, date}]

    for g in games:
        meta      = g["meta"]
        pre       = g["pre"]
        noshows   = g["noshows"]

        n_pre     = len(pre)
        n_noshow  = len(noshows)
        n_sold    = n_pre - n_noshow
        noshow_rate = round(n_noshow / n_pre * 100, 1) if n_pre else 0

        dead_value = sum(
            float(r["price_usd"]) for r in noshows if r.get("price_usd")
        )

        per_game.append({
            "date":         meta.get("game_date", ""),
            "label":        f"vs {meta.get('opponent','?')} ({meta.get('game_date','')[-5:]})",
            "opponent":     meta.get("opponent", ""),
            "day_of_week":  meta.get("day_of_week", ""),
            "pre_seats":    n_pre,
            "noshow_seats": n_noshow,
            "sold_seats":   n_sold,
            "noshow_rate":  noshow_rate,
            "dead_value":   round(dead_value, 2),
            "avg_price":    round(dead_value / n_noshow, 2) if n_noshow else 0,
            "opp_win_pct":  meta.get("opponent_win_pct", 0),
            "opp_wins":     meta.get("opponent_wins", 0),
            "opp_losses":   meta.get("opponent_losses", 0),
            "game_appeal":  meta.get("game_appeal_score", 0),
            "home_draw":    meta.get("home_draw_score", 0),
            "opp_draw":     meta.get("opponent_draw_score", 0),
            "folder":       g["folder"],
        })

        # Section breakdown (aggregate)
        game_sec = {}
        for r in pre:
            sec = r.get("section", "Unknown")
            section_data.setdefault(sec, {"pre": 0, "noshow": 0})
            section_data[sec]["pre"] += 1
            game_sec.setdefault(sec, {"pre": 0, "noshow": 0})
            game_sec[sec]["pre"] += 1

        for r in noshows:
            sec = r.get("section", "Unknown")
            section_data.setdefault(sec, {"pre": 0, "noshow": 0})
            section_data[sec]["noshow"] += 1
            game_sec.setdefault(sec, {"pre": 0, "noshow": 0})
            game_sec[sec]["noshow"] += 1

        # Per-game section summary (top 15 by noshow rate)
        gsec_list = []
        for s, d in game_sec.items():
            if d["pre"] > 0:
                rate = round(d["noshow"] / d["pre"] * 100, 1)
                gsec_list.append({"section": s, "rate": rate, "noshow": d["noshow"], "pre": d["pre"]})
        gsec_list.sort(key=lambda x: -x["rate"])
        per_game[-1]["section_summary"] = gsec_list[:15]

        # Day of week
        dow = meta.get("day_of_week", "Unknown")
        dow_data.setdefault(dow, {"games": 0, "total_pct": 0, "total_value": 0})
        dow_data[dow]["games"]       += 1
        dow_data[dow]["total_pct"]   += noshow_rate
        dow_data[dow]["total_value"] += dead_value

        # Opponent correlation
        opp_corr.append({
            "opp_win_pct":  meta.get("opponent_win_pct", 0),
            "noshow_rate":  noshow_rate,
            "dead_value":   round(dead_value, 2),
            "opponent":     meta.get("opponent", ""),
            "date":         meta.get("game_date", ""),
        })

    # Section summary — sort by noshow rate desc, top 15
    section_summary = []
    for sec, d in section_data.items():
        if d["pre"] > 0:
            rate = round(d["noshow"] / d["pre"] * 100, 1)
            section_summary.append({"section": sec, "rate": rate, "noshow": d["noshow"], "pre": d["pre"]})
    section_summary.sort(key=lambda x: -x["rate"])
    section_summary = section_summary[:15]

    # Day of week averages
    dow_summary = []
    dow_order = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]
    for dow in dow_order:
        if dow in dow_data:
            d = dow_data[dow]
            dow_summary.append({
                "day":       dow[:3],
                "avg_rate":  round(d["total_pct"] / d["games"], 1),
                "avg_value": round(d["total_value"] / d["games"], 2),
                "games":     d["games"],
            })

    # Best / worst
    sorted_games = sorted(per_game, key=lambda x: x["noshow_rate"])
    best  = sorted_games[0]  if sorted_games else None
    worst = sorted_games[-1] if sorted_games else None

    totals = {
        "games":        len(per_game),
        "total_noshow": sum(g["noshow_seats"] for g in per_game),
        "total_pre":    sum(g["pre_seats"]    for g in per_game),
        "total_value":  round(sum(g["dead_value"] for g in per_game), 2),
        "avg_rate":     round(sum(g["noshow_rate"] for g in per_game) / len(per_game), 1) if per_game else 0,
        "avg_value":    round(sum(g["dead_value"] for g in per_game) / len(per_game), 2) if per_game else 0,
    }

    return {
        "per_game":        per_game,
        "section_summary": section_summary,
        "dow_summary":     dow_summary,
        "opp_corr":        opp_corr,
        "totals":          totals,
        "best":            best,
        "worst":           worst,
    }


# ── HTML generation ────────────────────────────────────────────────────────────

def generate_html(team_slug: str, data: dict) -> str:
    t        = data["totals"]
    pg       = data["per_game"]
    sec      = data["section_summary"]
    dow      = data["dow_summary"]
    corr     = data["opp_corr"]
    best     = data["best"]
    worst    = data["worst"]

    team_title = team_slug.title()
    now = datetime.now().strftime("%B %d, %Y %I:%M %p")

    # Chart data as JSON
    game_labels    = json.dumps([g["label"] for g in pg])
    game_noshow    = json.dumps([g["noshow_rate"] for g in pg])
    game_value     = json.dumps([g["dead_value"] for g in pg])
    game_pre       = json.dumps([g["pre_seats"] for g in pg])
    game_ns_seats  = json.dumps([g["noshow_seats"] for g in pg])

    sec_labels = json.dumps([s["section"] for s in sec])
    sec_rates  = json.dumps([s["rate"] for s in sec])
    sec_counts = json.dumps([s["noshow"] for s in sec])

    # Per-game full data for JS dropdown
    per_game_js = json.dumps([{
        "label":       g["label"],
        "opponent":    g["opponent"],
        "date":        g["date"],
        "day":         g["day_of_week"],
        "pre_seats":   g["pre_seats"],
        "noshow_seats":g["noshow_seats"],
        "noshow_rate": g["noshow_rate"],
        "dead_value":  g["dead_value"],
        "avg_price":   g["avg_price"],
        "opp_win_pct": g["opp_win_pct"],
        "opp_wins":    g["opp_wins"],
        "opp_losses":  g["opp_losses"],
        "game_appeal": g["game_appeal"],
        "sec_labels":  [s["section"] for s in g.get("section_summary", [])],
        "sec_rates":   [s["rate"]    for s in g.get("section_summary", [])],
        "sec_counts":  [s["noshow"]  for s in g.get("section_summary", [])],
        "stadium_url": f"stadium_{team_slug}_{g['folder']}.html",
    } for g in pg])

    dow_labels = json.dumps([d["day"] for d in dow])
    dow_rates  = json.dumps([d["avg_rate"] for d in dow])
    dow_values = json.dumps([d["avg_value"] for d in dow])

    corr_data = json.dumps([{
        "x": c["opp_win_pct"],
        "y": c["noshow_rate"],
        "label": c["opponent"],
        "value": c["dead_value"],
        "date": c["date"],
    } for c in corr])

    best_str  = f'vs {best["opponent"]} ({best["date"]}) — {best["noshow_rate"]}% no-show rate'  if best  else "N/A"
    worst_str = f'vs {worst["opponent"]} ({worst["date"]}) — {worst["noshow_rate"]}% no-show rate' if worst else "N/A"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Fan XP — {team_title} Analytics Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet"/>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    :root{{
      --bg:#04060f;--bg2:#080d1a;--card:#0b1221;
      --cyan:#00f5ff;--cyan-dim:rgba(0,245,255,.08);--cyan-mid:rgba(0,245,255,.18);
      --gold:#ffd700;--purple:#7b61ff;--red:#ff4d6d;--green:#00e5a0;
      --white:#eef2ff;--muted:rgba(220,228,255,.5);--border:rgba(0,245,255,.14);
      --font:'Inter',sans-serif;
    }}
    html{{scroll-behavior:smooth}}
    body{{font-family:var(--font);background:var(--bg);color:var(--white);line-height:1.6;-webkit-font-smoothing:antialiased}}
    a{{text-decoration:none;color:inherit}}
    ::-webkit-scrollbar{{width:5px}}
    ::-webkit-scrollbar-thumb{{background:rgba(0,245,255,.3);border-radius:5px}}

    /* NAV */
    .nav{{position:sticky;top:0;z-index:99;background:rgba(4,6,15,.9);backdrop-filter:blur(20px);border-bottom:1px solid var(--border);padding:16px 28px;display:flex;align-items:center;justify-content:space-between}}
    .nav-logo{{font-size:1.3rem;font-weight:900;letter-spacing:-.04em}}
    .nav-logo span{{color:var(--cyan);text-shadow:0 0 14px var(--cyan)}}
    .nav-meta{{font-size:.75rem;color:var(--muted)}}
    .nav-back{{font-size:.8rem;color:var(--cyan);border:1px solid var(--border);padding:6px 14px;border-radius:999px;transition:background .2s}}
    .nav-back:hover{{background:var(--cyan-dim)}}

    /* LAYOUT */
    .page{{max-width:1280px;margin:0 auto;padding:40px 28px 80px}}
    .page-title{{font-size:clamp(1.8rem,3vw,2.6rem);font-weight:900;letter-spacing:-.03em;margin-bottom:6px}}
    .page-title span{{color:var(--cyan)}}
    .page-sub{{color:var(--muted);font-size:.9rem;margin-bottom:24px}}

    /* Game selector */
    .game-selector-row{{display:flex;align-items:center;gap:14px;margin-bottom:36px;flex-wrap:wrap}}
    .game-selector-row label{{font-size:.75rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}}
    .game-select{{
      background:var(--card);border:1px solid var(--border);border-radius:999px;
      color:var(--white);font-family:var(--font);font-size:.85rem;font-weight:600;
      padding:9px 20px;outline:none;cursor:pointer;transition:border-color .2s;
      appearance:none;-webkit-appearance:none;
      background-image:url("data:image/svg+xml,%3Csvg width='10' height='6' viewBox='0 0 10 6' fill='none' xmlns='http://www.w3.org/2000/svg'%3E%3Cpath d='M1 1l4 4 4-4' stroke='%2300f5ff' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
      background-repeat:no-repeat;background-position:right 16px center;padding-right:36px;
    }}
    .game-select:focus{{border-color:rgba(0,245,255,.4)}}
    .game-badge-row{{display:flex;gap:10px;flex-wrap:wrap}}
    .g-badge{{font-size:.72rem;font-weight:700;padding:4px 12px;border-radius:999px;border:1px solid var(--border);color:var(--muted)}}
    .g-badge span{{color:var(--cyan)}}
    .btn-stadium{{
      display:none;align-items:center;gap:8px;
      background:var(--cyan);color:#04060f;
      font-weight:800;font-size:.85rem;padding:9px 20px;
      border-radius:999px;border:none;cursor:pointer;
      box-shadow:0 0 20px rgba(0,245,255,.35);
      transition:box-shadow .2s,transform .2s;
      text-decoration:none;
    }}
    .btn-stadium:hover{{box-shadow:0 0 32px rgba(0,245,255,.6);transform:translateY(-1px)}}
    .btn-stadium.visible{{display:inline-flex}}

    /* KPI CARDS */
    .kpi-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:40px}}
    .kpi{{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:22px 24px;position:relative;overflow:hidden}}
    .kpi::before{{content:'';position:absolute;inset:0;background:var(--cyan-dim);opacity:0;transition:opacity .2s}}
    .kpi:hover::before{{opacity:1}}
    .kpi-label{{font-size:.7rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:10px}}
    .kpi-value{{font-size:2rem;font-weight:900;letter-spacing:-.03em;line-height:1}}
    .kpi-value.cyan{{color:var(--cyan);text-shadow:0 0 20px rgba(0,245,255,.4)}}
    .kpi-value.gold{{color:var(--gold);text-shadow:0 0 20px rgba(255,215,0,.4)}}
    .kpi-value.red{{color:var(--red)}}
    .kpi-value.green{{color:var(--green)}}
    .kpi-sub{{font-size:.75rem;color:var(--muted);margin-top:6px}}

    /* BEST/WORST STRIP */
    .bw-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:40px}}
    .bw-card{{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:20px 24px;display:flex;align-items:center;gap:16px}}
    .bw-icon{{font-size:1.8rem;flex-shrink:0}}
    .bw-label{{font-size:.68rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:4px}}
    .bw-val{{font-size:.95rem;font-weight:700;color:var(--white)}}

    /* CHARTS */
    .charts-grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px}}
    .chart-card{{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:24px}}
    .chart-card.wide{{grid-column:1/-1}}
    .chart-title{{font-size:.8rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--muted);margin-bottom:4px}}
    .chart-desc{{font-size:.78rem;color:rgba(220,228,255,.35);margin-bottom:20px}}
    .chart-wrap{{position:relative;height:260px}}
    .chart-wrap.tall{{height:320px}}

    @media(max-width:900px){{
      .kpi-grid{{grid-template-columns:repeat(2,1fr)}}
      .charts-grid{{grid-template-columns:1fr}}
      .chart-card.wide{{grid-column:auto}}
      .bw-grid{{grid-template-columns:1fr}}
    }}
  </style>
</head>
<body>

<nav class="nav">
  <div style="display:flex;align-items:center;gap:20px">
    <a href="index.html" class="nav-logo">Fan<span> XP</span></a>
    <a href="nba.html" class="nav-back">← NBA Teams</a>
  </div>
  <div class="nav-meta">Last updated: {now}</div>
</nav>

<div class="page">
  <div class="page-title">Orlando <span>{team_title}</span> — Seat Intelligence</div>
  <div class="page-sub">{t["games"]} game{'s' if t['games']!=1 else ''} tracked &nbsp;·&nbsp; {t["total_pre"]:,} pre-game seats &nbsp;·&nbsp; Kia Center, Orlando</div>

  <!-- Game selector -->
  <div class="game-selector-row">
    <label>Viewing</label>
    <select class="game-select" id="gameSelect" onchange="switchGame(this.value)">
      <option value="all">All Games (Aggregate)</option>
      {''.join(f'<option value="{i}">{g["label"]}</option>' for i, g in enumerate(pg))}
    </select>
    <div class="game-badge-row" id="gameBadges"></div>
    <a id="stadiumBtn" class="btn-stadium" href="#" target="_blank">
      🏟 View 3D Stadium
    </a>
  </div>

  <!-- KPIs -->
  <div class="kpi-grid">
    <div class="kpi" id="kpiValue">
      <div class="kpi-label">Dead Inventory / Game</div>
      <div class="kpi-value gold">${t["avg_value"]:,.0f}</div>
      <div class="kpi-sub">Total across {t["games"]} games: ${t["total_value"]:,.0f}</div>
    </div>
    <div class="kpi" id="kpiRate">
      <div class="kpi-label">No-Show Rate</div>
      <div class="kpi-value cyan">{t["avg_rate"]}%</div>
      <div class="kpi-sub">{t["total_noshow"]:,} no-show seats total</div>
    </div>
    <div class="kpi" id="kpiBest">
      <div class="kpi-label">Best Game</div>
      <div class="kpi-value green">{best["noshow_rate"] if best else "—"}%</div>
      <div class="kpi-sub">{best["opponent"] if best else "N/A"} · {best["date"] if best else ""}</div>
    </div>
    <div class="kpi" id="kpiWorst">
      <div class="kpi-label">Worst Game</div>
      <div class="kpi-value red">{worst["noshow_rate"] if worst else "—"}%</div>
      <div class="kpi-sub">{worst["opponent"] if worst else "N/A"} · {worst["date"] if worst else ""}</div>
    </div>
  </div>

  <!-- Best / Worst strip -->
  <div class="bw-grid">
    <div class="bw-card">
      <div class="bw-icon">🏆</div>
      <div>
        <div class="bw-label">Best Performing Game</div>
        <div class="bw-val">{best_str}</div>
      </div>
    </div>
    <div class="bw-card">
      <div class="bw-icon">⚠️</div>
      <div>
        <div class="bw-label">Worst Performing Game</div>
        <div class="bw-val">{worst_str}</div>
      </div>
    </div>
  </div>

  <!-- Charts row 1 -->
  <div class="charts-grid">

    <div class="chart-card wide">
      <div class="chart-title">Dead Inventory Value per Game</div>
      <div class="chart-desc">Total face value of confirmed no-show seats at halftime</div>
      <div class="chart-wrap"><canvas id="valueChart"></canvas></div>
    </div>

    <div class="chart-card">
      <div class="chart-title">No-Show Rate per Game</div>
      <div class="chart-desc">% of pre-game listed seats still available at halftime</div>
      <div class="chart-wrap"><canvas id="rateChart"></canvas></div>
    </div>

    <div class="chart-card">
      <div class="chart-title">Pre-Game vs No-Show Seats</div>
      <div class="chart-desc">Volume breakdown per game</div>
      <div class="chart-wrap"><canvas id="volumeChart"></canvas></div>
    </div>

    <div class="chart-card">
      <div class="chart-title">No-Show Rate by Section</div>
      <div class="chart-desc">Top 15 sections ranked by no-show % (avg across all games)</div>
      <div class="chart-wrap tall"><canvas id="sectionChart"></canvas></div>
    </div>

    <div class="chart-card">
      <div class="chart-title">Day of Week Breakdown</div>
      <div class="chart-desc">Avg no-show rate by day — more data = more reliable</div>
      <div class="chart-wrap"><canvas id="dowChart"></canvas></div>
    </div>

    <div class="chart-card">
      <div class="chart-title">Opponent Strength Correlation</div>
      <div class="chart-desc">Opponent win % vs no-show rate — does a better opponent = fewer no-shows?</div>
      <div class="chart-wrap"><canvas id="corrChart"></canvas></div>
    </div>

  </div>
</div>

<script>
const PER_GAME  = {per_game_js};
const ALL_GAMES = {{
  gameLabels:  {game_labels},
  gameNoshow:  {game_noshow},
  gameValue:   {game_value},
  gamePre:     {game_pre},
  gameNsSeats: {game_ns_seats},
  secLabels:   {sec_labels},
  secRates:    {sec_rates},
  secCounts:   {sec_counts},
  dowLabels:   {dow_labels},
  dowRates:    {dow_rates},
  corrData:    {corr_data},
}};

const CYAN   = 'rgba(0,245,255,0.85)';
const CYAN_D = 'rgba(0,245,255,0.15)';
const GOLD   = 'rgba(255,215,0,0.85)';
const GOLD_D = 'rgba(255,215,0,0.15)';
const RED    = 'rgba(255,77,109,0.85)';
const RED_D  = 'rgba(255,77,109,0.15)';
const GREEN  = 'rgba(0,229,160,0.85)';
const MUTED  = 'rgba(220,228,255,0.5)';
const BORDER = 'rgba(0,245,255,0.1)';

Chart.defaults.color = MUTED;
Chart.defaults.borderColor = BORDER;
Chart.defaults.font.family = 'Inter';
Chart.defaults.font.size   = 12;

const gridOpts = {{ color: BORDER, drawBorder: false }};
const tooltipOpts = {{
  backgroundColor: 'rgba(8,13,26,0.95)',
  borderColor: 'rgba(0,245,255,0.3)',
  borderWidth: 1,
  titleColor: '#eef2ff',
  bodyColor: MUTED,
  padding: 12,
  cornerRadius: 8,
}};

let gameLabels   = ALL_GAMES.gameLabels;
let gameNoshow   = ALL_GAMES.gameNoshow;
let gameValue    = ALL_GAMES.gameValue;
let gamePre      = ALL_GAMES.gamePre;
let gameNsSeats  = ALL_GAMES.gameNsSeats;
let secLabels    = ALL_GAMES.secLabels;
let secRates     = ALL_GAMES.secRates;
let secCounts    = ALL_GAMES.secCounts;
let dowLabels    = ALL_GAMES.dowLabels;
let dowRates     = ALL_GAMES.dowRates;
let corrData     = ALL_GAMES.corrData;

// ── KPI helpers ───────────────────────────────────────────────────────────────
function updateKPIs(mode) {{
  if (mode === 'all') {{
    const total = PER_GAME.reduce((a,g) => a + g.dead_value, 0);
    const avgVal = total / PER_GAME.length;
    const avgRate = PER_GAME.reduce((a,g) => a + g.noshow_rate, 0) / PER_GAME.length;
    const sorted = [...PER_GAME].sort((a,b) => a.noshow_rate - b.noshow_rate);
    const best = sorted[0], worst = sorted[sorted.length-1];
    setKPI('kpiValue', '$' + Math.round(avgVal).toLocaleString(), 'gold');
    setKPI('kpiRate',  avgRate.toFixed(1) + '%', 'cyan');
    setKPI('kpiBest',  best.noshow_rate + '%', 'green', best.opponent + ' · ' + best.date);
    setKPI('kpiWorst', worst.noshow_rate + '%', 'red', worst.opponent + ' · ' + worst.date);
    setKPI('kwBest',  '🏆 Best: ' + best.opponent + ' (' + best.date + ') — ' + best.noshow_rate + '% no-show rate');
    setKPI('kwWorst', '⚠️ Worst: ' + worst.opponent + ' (' + worst.date + ') — ' + worst.noshow_rate + '% no-show rate');
    document.getElementById('gameBadges').innerHTML = '';
  }} else {{
    const g = PER_GAME[parseInt(mode)];
    setKPI('kpiValue', '$' + Math.round(g.dead_value).toLocaleString(), 'gold');
    setKPI('kpiRate',  g.noshow_rate + '%', 'cyan');
    setKPI('kpiBest',  g.pre_seats + ' seats', 'green', 'listed pre-game');
    setKPI('kpiWorst', g.noshow_seats + ' seats', 'red', 'confirmed no-shows');
    document.getElementById('gameBadges').innerHTML = [
      `<div class='g-badge'>📅 ${{g.day}}, ${{g.date}}</div>`,
      `<div class='g-badge'>⚔️ vs ${{g.opponent}}</div>`,
      `<div class='g-badge'>📊 Opp W%: <span>${{(g.opp_win_pct*100).toFixed(1)}}%</span> (${{g.opp_wins}}-${{g.opp_losses}})</div>`,
      `<div class='g-badge'>💰 Avg no-show price: <span>$${{g.avg_price}}</span></div>`,
      `<div class='g-badge'>🎯 Appeal score: <span>${{g.game_appeal}}</span></div>`,
    ].join('');
  }}
}}

function setKPI(id, val, color, sub) {{
  const el = document.getElementById(id);
  if (!el) return;
  el.querySelector('.kpi-value').className = 'kpi-value ' + (color||'');
  el.querySelector('.kpi-value').textContent = val;
  if (sub !== undefined) el.querySelector('.kpi-sub').textContent = sub;
}}

// ── switchGame ────────────────────────────────────────────────────────────────
function switchGame(val) {{
  if (val === 'all') {{
    gameLabels  = ALL_GAMES.gameLabels;
    gameNoshow  = ALL_GAMES.gameNoshow;
    gameValue   = ALL_GAMES.gameValue;
    gamePre     = ALL_GAMES.gamePre;
    gameNsSeats = ALL_GAMES.gameNsSeats;
    secLabels   = ALL_GAMES.secLabels;
    secRates    = ALL_GAMES.secRates;
    secCounts   = ALL_GAMES.secCounts;
    dowLabels   = ALL_GAMES.dowLabels;
    dowRates    = ALL_GAMES.dowRates;
    corrData    = ALL_GAMES.corrData;
  }} else {{
    const g = PER_GAME[parseInt(val)];
    gameLabels  = [g.label];
    gameNoshow  = [g.noshow_rate];
    gameValue   = [g.dead_value];
    gamePre     = [g.pre_seats];
    gameNsSeats = [g.noshow_seats];
    secLabels   = g.sec_labels;
    secRates    = g.sec_rates;
    secCounts   = g.sec_counts;
    dowLabels   = [g.day.substring(0,3)];
    dowRates    = [g.noshow_rate];
    corrData    = [{{ x: g.opp_win_pct, y: g.noshow_rate, label: g.opponent, value: g.dead_value, date: g.date }}];
  }}
  updateKPIs(val);
  refreshCharts();
  const btn = document.getElementById('stadiumBtn');
  if (val === 'all') {{
    btn.classList.remove('visible');
  }} else {{
    const g = PER_GAME[parseInt(val)];
    btn.href = g.stadium_url;
    btn.classList.add('visible');
  }}
}}

function refreshCharts() {{
  [valueChart, rateChart, volumeChart, sectionChart, dowChart, corrChart].forEach(c => c.destroy());
  buildCharts();
}}

let valueChart, rateChart, volumeChart, sectionChart, dowChart, corrChart;

function buildCharts() {{

// 1 — Dead inventory value per game
valueChart = new Chart(document.getElementById('valueChart'), {{
  type: 'bar',
  data: {{
    labels: gameLabels,
    datasets: [{{
      label: 'Dead Inventory ($)',
      data: gameValue,
      backgroundColor: GOLD_D,
      borderColor: GOLD,
      borderWidth: 2,
      borderRadius: 8,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ ...tooltipOpts, callbacks: {{
      label: ctx => ` ${{ctx.parsed.y.toLocaleString('en-US', {{style:'currency',currency:'USD',maximumFractionDigits:0}})}}`
    }}}} }},
    scales: {{ x: {{ grid: gridOpts }}, y: {{ grid: gridOpts, ticks: {{ callback: v => '$'+v.toLocaleString() }} }} }}
  }}
}});

// 2 — No-show rate per game
const rateColors = gameNoshow.map(v => v === Math.max(...gameNoshow) ? RED : v === Math.min(...gameNoshow) ? GREEN : CYAN);
rateChart = new Chart(document.getElementById('rateChart'), {{
  type: 'bar',
  data: {{
    labels: gameLabels,
    datasets: [{{
      label: 'No-Show Rate',
      data: gameNoshow,
      backgroundColor: rateColors.map(c => c.replace('0.85','0.2')),
      borderColor: rateColors,
      borderWidth: 2,
      borderRadius: 8,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ ...tooltipOpts, callbacks: {{ label: ctx => ` ${{ctx.parsed.y}}%` }} }} }},
    scales: {{ x: {{ grid: gridOpts }}, y: {{ grid: gridOpts, ticks: {{ callback: v => v+'%' }}, max: 100 }} }}
  }}
}});

// 3 — Volume stacked bar
volumeChart = new Chart(document.getElementById('volumeChart'), {{
  type: 'bar',
  data: {{
    labels: gameLabels,
    datasets: [
      {{ label: 'No-Shows', data: gameNsSeats, backgroundColor: 'rgba(255,77,109,0.7)', borderRadius: 4 }},
      {{ label: 'Sold/Attended', data: gamePre.map((v,i) => v - gameNsSeats[i]), backgroundColor: 'rgba(0,229,160,0.5)', borderRadius: 4 }},
    ]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ labels: {{ color: MUTED }} }}, tooltip: tooltipOpts }},
    scales: {{ x: {{ stacked: true, grid: gridOpts }}, y: {{ stacked: true, grid: gridOpts }} }}
  }}
}});

// 4 — Section breakdown (horizontal bar)
sectionChart = new Chart(document.getElementById('sectionChart'), {{
  type: 'bar',
  data: {{
    labels: secLabels,
    datasets: [{{
      label: 'No-Show %',
      data: secRates,
      backgroundColor: secRates.map(v => v > 70 ? RED_D : v > 40 ? GOLD_D : CYAN_D),
      borderColor:     secRates.map(v => v > 70 ? RED   : v > 40 ? GOLD   : CYAN),
      borderWidth: 2,
      borderRadius: 4,
    }}]
  }},
  options: {{
    indexAxis: 'y',
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ ...tooltipOpts, callbacks: {{ label: ctx => ` ${{ctx.parsed.x}}% (${{secCounts[ctx.dataIndex]}} seats)` }} }} }},
    scales: {{ x: {{ grid: gridOpts, ticks: {{ callback: v => v+'%' }}, max: 100 }}, y: {{ grid: gridOpts }} }}
  }}
}});

// 5 — Day of week
dowChart = new Chart(document.getElementById('dowChart'), {{
  type: 'bar',
  data: {{
    labels: {dow_labels},
    datasets: [{{
      label: 'Avg No-Show %',
      data: {dow_rates},
      backgroundColor: CYAN_D,
      borderColor: CYAN,
      borderWidth: 2,
      borderRadius: 8,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{ legend: {{ display: false }}, tooltip: {{ ...tooltipOpts, callbacks: {{ label: ctx => ` ${{ctx.parsed.y}}%` }} }} }},
    scales: {{ x: {{ grid: gridOpts }}, y: {{ grid: gridOpts, ticks: {{ callback: v => v+'%' }}, max: 100 }} }}
  }}
}});

// 6 — Opponent strength scatter
corrChart = new Chart(document.getElementById('corrChart'), {{
  type: 'scatter',
  data: {{
    datasets: [{{
      label: 'Game',
      data: corrData,
      backgroundColor: CYAN_D,
      borderColor: CYAN,
      borderWidth: 2,
      pointRadius: 10,
      pointHoverRadius: 13,
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{ ...tooltipOpts, callbacks: {{
        title: items => corrData[items[0].dataIndex].label,
        label: ctx => [
          ` Opp win %: ${{(ctx.parsed.x*100).toFixed(1)}}%`,
          ` No-show rate: ${{ctx.parsed.y}}%`,
          ` Dead value: $${{corrData[ctx.dataIndex].value.toLocaleString()}}`,
        ]
      }}}}
    }},
    scales: {{
      x: {{ grid: gridOpts, title: {{ display: true, text: 'Opponent Win %', color: MUTED }}, ticks: {{ callback: v => (v*100).toFixed(0)+'%' }} }},
      y: {{ grid: gridOpts, title: {{ display: true, text: 'No-Show Rate', color: MUTED }}, ticks: {{ callback: v => v+'%' }}, max: 100 }}
    }}
  }}
}});

}} // end buildCharts

buildCharts();
updateKPIs('all');
</script>
</body>
</html>"""


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) != 2:
        print("Usage: python generate_dashboard.py <team_slug>")
        print("  e.g. python generate_dashboard.py magic")
        sys.exit(1)

    team_slug = sys.argv[1].lower()
    print(f"Loading data for '{team_slug}'...")

    games = load_games(team_slug)
    if not games:
        print(f"No complete games found for '{team_slug}' (need game_meta.json + no_shows.csv)")
        sys.exit(1)

    print(f"  Found {len(games)} complete game(s)")
    data = analyse(games)

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, f"dashboard_{team_slug}.html")
    html = generate_html(team_slug, data)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"  Dashboard saved → {out_path}")


if __name__ == "__main__":
    main()
