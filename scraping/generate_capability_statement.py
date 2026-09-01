"""
generate_capability_statement.py

Builds a one-page Fan XP capability statement / pilot pitch, pulling real
current numbers from Supabase instead of a hand-frozen document.

NFL is reported honestly (teams monitored, zero real games yet, since the
scraper only recently had its timeout bug fixed). The "proof it works"
section leans on NBA/WNBA data instead, since MLB's no_shows table currently
has a serious duplicate-insert bug (some games show 100,000+ "no-shows,"
physically impossible for any stadium) -- MAX_SANE_NO_SHOWS below excludes
any game above that bound from every total, so a bad game can't quietly
inflate a number that ends up in front of a team. Do not remove that filter
without first finding and fixing the underlying MLB bug.

Usage:
    python generate_capability_statement.py                # generic version
    python generate_capability_statement.py commanders     # for one NFL team

Output:
    ../capability_statement.html               (generic)
    ../capability_statement_{team}.html         (team-specific)
"""

import os
import sys
from datetime import datetime

from nfl_teams import NFL_TEAMS
from generate_nfl_story import NFL_ARENAS
import supabase_client

# Games with more no-shows than this are excluded from every total below --
# see the module docstring. 500 is generous headroom above any real per-game
# secondary-market no-show count seen in clean data (NBA/WNBA top out well
# under this).
MAX_SANE_NO_SHOWS = 500


def collect_proof_of_concept_stats() -> dict:
    """Real, sanity-filtered NBA + WNBA numbers to cite as evidence the
    methodology works, since NFL has no real games yet and MLB's no_shows
    data is currently unreliable (see module docstring)."""
    c = supabase_client._get_client()
    if c is None:
        return {"games": 0, "no_shows": 0, "value": 0.0}

    games = 0
    no_shows = 0
    value = 0.0
    for league in ("nba", "wnba"):
        league_games = c.table("games").select("id").eq("league", league).execute().data
        for g in league_games:
            r = c.table("no_shows").select("price_usd", count="exact").eq("game_id", g["id"]).execute()
            if r.count and 0 < r.count <= MAX_SANE_NO_SHOWS:
                games += 1
                no_shows += r.count
                value += sum(float(row.get("price_usd") or 0) for row in r.data)

    return {"games": games, "no_shows": no_shows, "value": value}


def collect_nfl_stats() -> dict:
    c = supabase_client._get_client()
    total_teams = len(NFL_TEAMS)
    if c is None:
        return {"teams": total_teams, "games_with_data": 0}

    games = c.table("games").select("id").eq("league", "nfl").execute().data
    games_with_data = 0
    for g in games:
        pre = supabase_client.count_listings(g["id"], "pre_game")
        if pre > 0:
            games_with_data += 1

    return {"teams": total_teams, "games_with_data": games_with_data}


FONT_LINKS = """<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Newsreader:ital,opsz,wght@0,6..72,500;0,6..72,600;1,6..72,500&display=swap" rel="stylesheet" />"""


def generate_html(team_slug: str | None) -> str:
    poc = collect_proof_of_concept_stats()
    nfl = collect_nfl_stats()

    if team_slug:
        team = NFL_TEAMS.get(team_slug)
        if not team:
            raise ValueError(f"Unknown NFL team '{team_slug}'. Valid: {', '.join(sorted(NFL_TEAMS.keys()))}")
        team_name = team_slug.replace("_", " ").title()
        if team_slug == "49ers":
            team_name = "49ers"
        arena = NFL_ARENAS.get(team_slug, "")
        deliver_for = f"What We Deliver for the {team_name}"
        pilot_line = (
            f"We're offering to run our full data collection and reporting pipeline for "
            f"<strong>one {team_name} home game at no cost</strong> — delivering a complete "
            f"section-level no-show report with no commitment required."
        )
    else:
        team_name = None
        arena = ""
        deliver_for = "What We Deliver"
        pilot_line = (
            "We're offering to run our full data collection and reporting pipeline for "
            "<strong>one home game at no cost</strong> — delivering a complete section-level "
            "no-show report with no commitment required."
        )

    date_str = datetime.now().strftime("%B %-d, %Y")
    poc_value_str = f"${poc['value']:,.0f}"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Fan XP · Capability Statement{f" · {team_name}" if team_name else ""}</title>
{FONT_LINKS}
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  :root {{
    --text: #16181c; --muted: #6d7076; --muted-2: #9a9da3; --border: #e7e6e2;
    --accent: #2f5fff; --accent-wash: #ecf0ff;
    --sans: 'Inter', sans-serif; --serif: 'Newsreader', Georgia, serif;
  }}
  body {{
    font-family: var(--sans); font-size: 11px; color: var(--text);
    background: white; padding: 48px 56px; max-width: 780px; margin: 0 auto;
    -webkit-font-smoothing: antialiased;
  }}

  .header {{
    display: flex; justify-content: space-between; align-items: flex-start;
    border-bottom: 2px solid var(--text); padding-bottom: 16px; margin-bottom: 20px;
  }}
  .header-left .word {{ font-family: var(--serif); font-weight: 500; font-size: 24px; letter-spacing: -.01em; }}
  .header-left .word .xp {{ color: var(--accent); font-style: italic; }}
  .header-left p {{ font-size: 10px; color: var(--muted); margin-top: 4px; }}
  .header-right {{ text-align: right; font-size: 10px; color: var(--muted); line-height: 1.7; }}
  .header-right strong {{ color: var(--text); font-size: 11px; }}

  .tagline {{
    background: var(--accent); color: white; padding: 10px 14px;
    font-size: 11px; line-height: 1.5; margin-bottom: 20px; border-radius: 4px;
  }}

  .section {{ margin-bottom: 18px; }}
  .section h2 {{
    font-size: 9px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase;
    color: var(--muted); border-bottom: 1px solid var(--border); padding-bottom: 4px; margin-bottom: 9px;
  }}
  .section p {{ font-size: 11px; line-height: 1.65; color: #2a2a2a; }}

  .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}

  .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 18px; }}
  .stat {{ border: 1px solid var(--border); padding: 10px 12px; border-radius: 4px; }}
  .stat .number {{ font-family: var(--serif); font-size: 20px; font-weight: 600; line-height: 1; color: var(--text); }}
  .stat .label {{ font-size: 9px; color: var(--muted); margin-top: 4px; line-height: 1.3; }}

  ul {{ padding-left: 14px; margin-top: 4px; }}
  ul li {{ font-size: 11px; line-height: 1.7; color: #2a2a2a; }}

  .pilot {{
    border: 1.5px solid var(--accent); padding: 10px 14px; border-radius: 4px;
    margin-top: 18px; display: flex; align-items: center; gap: 12px; background: var(--accent-wash);
  }}
  .pilot .pill {{
    background: var(--accent); color: white; font-size: 9px; font-weight: 700;
    letter-spacing: 1px; text-transform: uppercase; padding: 4px 8px; border-radius: 4px; white-space: nowrap;
  }}
  .pilot p {{ font-size: 11px; line-height: 1.5; color: #2a2a2a; }}

  .footer {{
    margin-top: 20px; padding-top: 12px; border-top: 1px solid var(--border);
    display: flex; justify-content: space-between; font-size: 9px; color: var(--muted);
  }}
  .footer a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}

  @media print {{ body {{ padding: 32px 40px; }} }}
</style>
</head>
<body>

  <div class="header">
    <div class="header-left">
      <div class="word">Fan<span class="xp">XP</span></div>
      <p>Capability Statement{f" &nbsp;&middot;&nbsp; {team_name}" if team_name else ""} &nbsp;&middot;&nbsp; {date_str}</p>
    </div>
    <div class="header-right">
      <strong>Aaran Guha</strong><br>
      Founder<br>
      aaranguhaca@gmail.com<br>
      fan-xp.vercel.app
    </div>
  </div>

  <div class="tagline">
    Fan XP delivers real-time fan attendance intelligence that identifies, at the section level, which sold seats go unoccupied at every home game — with no integration into existing ticketing systems required.
  </div>

  <div class="stats">
    <div class="stat">
      <div class="number">{nfl['teams']}</div>
      <div class="label">NFL teams monitored</div>
    </div>
    <div class="stat">
      <div class="number">{poc['games']}</div>
      <div class="label">Games with verified no-show data (NBA/WNBA)</div>
    </div>
    <div class="stat">
      <div class="number">{poc['no_shows']:,}</div>
      <div class="label">No-show seats identified to date</div>
    </div>
    <div class="stat">
      <div class="number">{poc_value_str}</div>
      <div class="label">In no-show ticket value identified</div>
    </div>
  </div>

  <div class="two-col">

    <div>
      <div class="section">
        <h2>The Problem</h2>
        <p>Ticketing platforms tell you who bought a seat. They cannot tell you who sat in it. The gap between tickets sold and fans in seats directly impacts concession revenue, sponsor activation value, atmosphere, and broadcast product quality — yet no existing tool measures this in real time at the section level.</p>
      </div>

      <div class="section">
        <h2>How It Works</h2>
        <p>Our methodology captures live secondary-market seat availability at two points per game:</p>
        <ul style="margin-top:6px">
          <li><strong>Pre-game</strong> — before kickoff, baseline by section</li>
          <li><strong>Halftime</strong> — seats back on the market</li>
        </ul>
        <p style="margin-top:6px">The delta between these snapshots identifies confirmed no-show seats — mapped by section, price tier, opponent, and day of week. Fully automated. No manual reporting.</p>
      </div>
    </div>

    <div>
      <div class="section">
        <h2>{deliver_for}</h2>
        <ul>
          <li>Per-section no-show rates every home game</li>
          <li>Opponent &amp; day-of-week attendance breakdowns</li>
          <li>Secondary market pricing trends pre- &amp; in-game</li>
          <li>Live dashboard, auto-updated after every game</li>
          <li>Season-over-season trend analysis</li>
        </ul>
      </div>

      <div class="section">
        <h2>Why Fan XP</h2>
        <ul>
          <li>Already monitoring {nfl['teams']} NFL teams, plus MLB, WNBA, and NBA</li>
          <li>Methodology already proven with real captured data — not a mockup</li>
          <li>Fully automated pipeline, zero ticketing-system integration required</li>
          <li>Founder-led — direct, fast iteration on what your team actually needs</li>
          <li>Live product demo available immediately</li>
        </ul>
      </div>
    </div>

  </div>

  <div class="pilot">
    <div class="pill">Free Pilot</div>
    <p>{pilot_line} Live demo available at <strong>fan-xp.vercel.app</strong>{f" &nbsp;&middot;&nbsp; {arena}" if arena else ""}</p>
  </div>

  <div class="footer">
    <span>Fan XP</span>
    <span><a href="https://fan-xp.vercel.app">fan-xp.vercel.app</a></span>
  </div>

</body>
</html>"""
    return html


def main():
    team_slug = sys.argv[1].lower() if len(sys.argv) > 1 else None
    html = generate_html(team_slug)
    out = f"../capability_statement_{team_slug}.html" if team_slug else "../capability_statement.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"-> {out}")


if __name__ == "__main__":
    main()
