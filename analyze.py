"""
analyze.py

Cross-game analytics across all collected data.

Answers:
  1. Average dead inventory value per game
  2. Average no-show rate by section
  3. Worst performing game (highest no-show rate)
  4. Best performing game (lowest no-show rate)
  5. Day of week breakdown
  6. Opponent strength correlation

Usage:
    python analyze.py
"""

import csv
import json
import os
from collections import defaultdict
from statistics import mean, median

DATA_DIR = "data"


# ── Data loading ──────────────────────────────────────────────────────────────

def load_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_games():
    """
    Walk data/ and collect every game that has pre_game.csv + no_shows.csv
    (halftime.csv optional for rate calc — we use pre_game as denominator).
    Returns list of game dicts with merged CSV + meta data.
    """
    games = []
    for team in sorted(os.listdir(DATA_DIR)):
        team_dir = os.path.join(DATA_DIR, team)
        if not os.path.isdir(team_dir):
            continue
        for game_folder in sorted(os.listdir(team_dir)):
            gdir = os.path.join(team_dir, game_folder)
            if not os.path.isdir(gdir):
                continue

            pre_path      = os.path.join(gdir, "pre_game.csv")
            no_show_path  = os.path.join(gdir, "no_shows.csv")
            meta_path     = os.path.join(gdir, "game_meta.json")

            if not os.path.isfile(pre_path) or not os.path.isfile(no_show_path):
                continue

            pre_rows      = load_csv(pre_path)
            no_show_rows  = load_csv(no_show_path)

            if not pre_rows or not no_show_rows:
                continue

            meta = {}
            if os.path.isfile(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)

            # Dead inventory value = sum of no-show seat prices
            prices = [float(r["price_usd"]) for r in no_show_rows if r.get("price_usd")]
            dead_value = sum(prices)
            no_show_rate = len(no_show_rows) / len(pre_rows) if pre_rows else 0

            games.append({
                "team":             team,
                "folder":           game_folder,
                "label":            f"{meta.get('opponent','?')} @ {team.title()}  ({meta.get('game_date','?')})",
                "pre_count":        len(pre_rows),
                "no_show_count":    len(no_show_rows),
                "no_show_rate":     no_show_rate,
                "dead_value":       dead_value,
                "avg_no_show_price": mean(prices) if prices else 0,
                "day_of_week":      meta.get("day_of_week", "Unknown"),
                "opponent":         meta.get("opponent", "Unknown"),
                "opponent_draw":    meta.get("opponent_draw_score"),
                "opponent_win_pct": meta.get("opponent_win_pct"),
                "game_appeal":      meta.get("game_appeal_score"),
                "pre_rows":         pre_rows,
                "no_show_rows":     no_show_rows,
            })

    return games


# ── Analysis ──────────────────────────────────────────────────────────────────

def avg_dead_inventory(games):
    values = [g["dead_value"] for g in games if g["dead_value"] > 0]
    return mean(values) if values else 0


def no_show_rate_by_section(games):
    """
    For each section, aggregate: total pre-game seats, total no-show seats → rate.
    Uses seat-level schema (section field present on every row).
    """
    section_pre     = defaultdict(int)
    section_no_show = defaultdict(int)

    for g in games:
        for r in g["pre_rows"]:
            sec = r.get("section", "").strip()
            if sec:
                section_pre[sec] += 1
        for r in g["no_show_rows"]:
            sec = r.get("section", "").strip()
            if sec:
                section_no_show[sec] += 1

    results = {}
    for sec, pre_count in section_pre.items():
        ns = section_no_show.get(sec, 0)
        results[sec] = {
            "pre_count":    pre_count,
            "no_show_count": ns,
            "no_show_rate":  ns / pre_count,
        }
    return results


def day_of_week_breakdown(games):
    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    by_day = defaultdict(list)
    for g in games:
        by_day[g["day_of_week"]].append(g)

    results = {}
    for day in day_order:
        if day not in by_day:
            continue
        gs = by_day[day]
        results[day] = {
            "game_count":        len(gs),
            "avg_no_show_rate":  mean(g["no_show_rate"] for g in gs),
            "avg_dead_value":    mean(g["dead_value"] for g in gs),
            "total_dead_value":  sum(g["dead_value"] for g in gs),
        }
    return results


def opponent_strength_correlation(games):
    """
    Group games by opponent draw score quartile and show no-show rates.
    Also show raw correlation between opponent_draw and no_show_rate.
    """
    scored = [g for g in games if g["opponent_draw"] is not None]
    if len(scored) < 2:
        return None

    draws = [g["opponent_draw"] for g in scored]
    rates = [g["no_show_rate"] for g in scored]

    # Pearson correlation
    n = len(draws)
    mx, my = mean(draws), mean(rates)
    cov = sum((x - mx) * (y - my) for x, y in zip(draws, rates)) / n
    sx  = (sum((x - mx)**2 for x in draws) / n) ** 0.5
    sy  = (sum((y - my)**2 for y in rates) / n) ** 0.5
    r   = cov / (sx * sy) if sx and sy else 0

    # Bin by opponent draw score
    bins = {"High (≥8.0)": [], "Mid (6–8)": [], "Low (<6)": []}
    for g in scored:
        d = g["opponent_draw"]
        if d >= 8.0:
            bins["High (≥8.0)"].append(g)
        elif d >= 6.0:
            bins["Mid (6–8)"].append(g)
        else:
            bins["Low (<6)"].append(g)

    bin_stats = {}
    for label, gs in bins.items():
        if not gs:
            continue
        bin_stats[label] = {
            "game_count":       len(gs),
            "avg_no_show_rate": mean(g["no_show_rate"] for g in gs),
            "avg_dead_value":   mean(g["dead_value"] for g in gs),
        }

    return {"pearson_r": r, "bins": bin_stats, "games": scored}


# ── Printing ──────────────────────────────────────────────────────────────────

def bar(rate, width=20):
    filled = round(rate * width)
    return "█" * filled + "░" * (width - filled)


def print_report(games):
    SEP  = "=" * 70
    SEP2 = "-" * 70

    print(f"\n{SEP}")
    print(f"  FAN XP — DEAD INVENTORY ANALYSIS   ({len(games)} games)")
    print(SEP)

    # ── 1. Average dead inventory value ───────────────────────────────────────
    avg_val  = avg_dead_inventory(games)
    avg_rate = mean(g["no_show_rate"] for g in games)
    avg_ns   = mean(g["no_show_count"] for g in games)
    total_dead = sum(g["dead_value"] for g in games)

    print(f"\n{'─'*70}")
    print("  1.  AVERAGE DEAD INVENTORY VALUE PER GAME")
    print(f"{'─'*70}")
    print(f"  Avg dead value (all games):    ${avg_val:>10,.0f}")
    print(f"  Total dead value (all games):  ${total_dead:>10,.0f}")
    print(f"  Avg no-show count:              {avg_ns:>9.1f} seats/game")
    print(f"  Avg no-show rate:               {avg_rate:>9.1%}")

    # ── 2. No-show rate by section (top 15 worst + best) ──────────────────────
    sec_data = no_show_rate_by_section(games)
    # Only sections with ≥10 pre-game appearances for statistical reliability
    reliable = {s: v for s, v in sec_data.items() if v["pre_count"] >= 10}
    sorted_sec = sorted(reliable.items(), key=lambda x: x[1]["no_show_rate"], reverse=True)

    print(f"\n{'─'*70}")
    print("  2.  NO-SHOW RATE BY SECTION  (sections with ≥10 observations)")
    print(f"{'─'*70}")
    print(f"  {'Section':<12}  {'Pre':>5}  {'No-Show':>7}  {'Rate':>6}  Chart")
    print(f"  {'─'*12}  {'─'*5}  {'─'*7}  {'─'*6}  {'─'*20}")

    top_n = 15
    worst = sorted_sec[:top_n]
    best  = sorted_sec[-top_n:][::-1]

    print(f"\n  Worst {top_n} sections (highest no-show rate):")
    for sec, v in worst:
        print(f"  {sec:<12}  {v['pre_count']:>5}  {v['no_show_count']:>7}  {v['no_show_rate']:>5.1%}  {bar(v['no_show_rate'])}")

    print(f"\n  Best {top_n} sections (lowest no-show rate):")
    for sec, v in best:
        print(f"  {sec:<12}  {v['pre_count']:>5}  {v['no_show_count']:>7}  {v['no_show_rate']:>5.1%}  {bar(v['no_show_rate'])}")

    # ── 3 & 4. Worst / Best games ─────────────────────────────────────────────
    ranked = sorted(games, key=lambda g: g["no_show_rate"], reverse=True)

    print(f"\n{'─'*70}")
    print("  3.  WORST PERFORMING GAMES  (highest no-show rate)")
    print(f"{'─'*70}")
    print(f"  {'Game':<45}  {'Rate':>6}  {'No-Shows':>8}  {'Dead $':>8}")
    print(f"  {'─'*45}  {'─'*6}  {'─'*8}  {'─'*8}")
    for g in ranked[:10]:
        print(f"  {g['label']:<45}  {g['no_show_rate']:>5.1%}  {g['no_show_count']:>8}  ${g['dead_value']:>7,.0f}")

    print(f"\n{'─'*70}")
    print("  4.  BEST PERFORMING GAMES  (lowest no-show rate)")
    print(f"{'─'*70}")
    print(f"  {'Game':<45}  {'Rate':>6}  {'No-Shows':>8}  {'Dead $':>8}")
    print(f"  {'─'*45}  {'─'*6}  {'─'*8}  {'─'*8}")
    for g in ranked[-10:][::-1]:
        print(f"  {g['label']:<45}  {g['no_show_rate']:>5.1%}  {g['no_show_count']:>8}  ${g['dead_value']:>7,.0f}")

    # ── 5. Day of week ─────────────────────────────────────────────────────────
    dow = day_of_week_breakdown(games)

    print(f"\n{'─'*70}")
    print("  5.  DAY OF WEEK BREAKDOWN")
    print(f"{'─'*70}")
    print(f"  {'Day':<12}  {'Games':>5}  {'Avg Rate':>8}  {'Avg Dead $':>10}  Chart")
    print(f"  {'─'*12}  {'─'*5}  {'─'*8}  {'─'*10}  {'─'*20}")
    for day, v in dow.items():
        print(f"  {day:<12}  {v['game_count']:>5}  {v['avg_no_show_rate']:>7.1%}  ${v['avg_dead_value']:>9,.0f}  {bar(v['avg_no_show_rate'])}")

    # ── 6. Opponent strength correlation ──────────────────────────────────────
    corr = opponent_strength_correlation(games)

    print(f"\n{'─'*70}")
    print("  6.  OPPONENT STRENGTH CORRELATION")
    print(f"{'─'*70}")
    if corr:
        r = corr["pearson_r"]
        direction = "negative" if r < 0 else "positive"
        strength  = "strong" if abs(r) > 0.5 else "moderate" if abs(r) > 0.3 else "weak"
        print(f"  Pearson r (draw score vs no-show rate): {r:+.3f}  [{strength} {direction}]")
        print(f"  (r < 0 means stronger opponents → fewer no-shows, as expected)")
        print()
        print(f"  {'Tier':<16}  {'Games':>5}  {'Avg Rate':>8}  {'Avg Dead $':>10}  Chart")
        print(f"  {'─'*16}  {'─'*5}  {'─'*8}  {'─'*10}  {'─'*20}")
        for tier, v in corr["bins"].items():
            print(f"  {tier:<16}  {v['game_count']:>5}  {v['avg_no_show_rate']:>7.1%}  ${v['avg_dead_value']:>9,.0f}  {bar(v['avg_no_show_rate'])}")

        # Show individual game scatter sorted by draw score
        print(f"\n  Game-level detail (sorted by opponent draw score ↓):")
        print(f"  {'Opponent':<30}  {'Draw':>5}  {'Win%':>5}  {'Rate':>6}  {'Dead $':>8}")
        print(f"  {'─'*30}  {'─'*5}  {'─'*5}  {'─'*6}  {'─'*8}")
        for g in sorted(corr["games"], key=lambda x: x["opponent_draw"], reverse=True):
            wp = f"{g['opponent_win_pct']:.0%}" if g["opponent_win_pct"] is not None else " N/A"
            print(f"  {g['opponent']:<30}  {g['opponent_draw']:>5.1f}  {wp:>5}  {g['no_show_rate']:>5.1%}  ${g['dead_value']:>7,.0f}")
    else:
        print("  Not enough games with draw score data.")

    print(f"\n{SEP}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    games = load_games()
    if not games:
        print("No complete games found (need pre_game.csv + no_shows.csv).")
    else:
        print_report(games)
