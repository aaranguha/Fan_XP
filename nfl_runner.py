"""
nfl_runner.py

Checks today's NFL schedule and launches nfl_run_game.py for each team
with a home game today. Mirrors daily_runner.py for NBA.

NFL season runs September–February.
Games are played Thursday nights, Sunday afternoons/nights, Monday nights.

Usage:
    python nfl_runner.py

Cron (via GitHub Actions — see .github/workflows/nfl.yml):
    Sundays    17:00 UTC (noon ET)
    Mondays    23:00 UTC (6 PM ET)
    Thursdays  23:00 UTC (6 PM ET)
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from nfl_teams import NFL_TEAMS, NFL_TRICODE_TO_SLUG

PYTHON = sys.executable


def espn_get_json(url: str, curl_attempts: int = 3) -> dict:
    """
    GET a JSON URL via curl.

    ESPN's CDN mostly returns 403 to requests/urllib (a TLS-fingerprint bot
    check, not a headers issue — verified requests/urllib fail while curl
    succeeds on the same request most of the time). curl isn't 100% reliable
    either (some edge nodes still 403 it occasionally), so we retry a few
    times before falling back to requests as a last resort.
    """
    last_err = None
    for attempt in range(curl_attempts):
        try:
            out = subprocess.run(
                ["curl", "-sS", "-A", "Mozilla/5.0", url],
                capture_output=True, text=True, timeout=15, check=True,
            )
            return json.loads(out.stdout)
        except Exception as e:
            last_err = e
            if attempt < curl_attempts - 1:
                time.sleep(2)

    print(f"  [espn_get_json] curl failed after {curl_attempts} attempts ({last_err}), falling back to requests...")
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_home_teams_espn(today: str) -> list[str]:
    """
    Fetch today's NFL home teams from ESPN's scoreboard API.

    ESPN's `dates=` param buckets games by UTC date, but evening ET kickoffs
    (e.g. Sunday/Thursday Night Football, 8:15-8:20 PM ET) land after midnight
    UTC — i.e. under *tomorrow's* UTC bucket even though they're still
    "today" in US Eastern time. So we query both today's and tomorrow's UTC
    date and keep only the events whose kickoff falls on `today` in ET.
    """
    tomorrow = (datetime.strptime(today, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    eastern = ZoneInfo("America/New_York")

    events_by_id = {}
    for d in (today, tomorrow):
        date_str = d.replace("-", "")
        url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates={date_str}"
        data = espn_get_json(url)
        for event in data.get("events", []):
            events_by_id[event["id"]] = event

    slugs = []
    for event in events_by_id.values():
        kickoff_utc = datetime.strptime(event["date"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=timezone.utc)
        local_date = kickoff_utc.astimezone(eastern).strftime("%Y-%m-%d")
        if local_date != today:
            continue
        comps = event["competitions"][0]
        home = next((t for t in comps["competitors"] if t["homeAway"] == "home"), None)
        away = next((t for t in comps["competitors"] if t["homeAway"] == "away"), None)
        if not home:
            continue
        tricode = home["team"]["abbreviation"]
        slug = NFL_TRICODE_TO_SLUG.get(tricode)
        if slug:
            home_name = home["team"]["displayName"]
            away_name = away["team"]["displayName"] if away else "?"
            print(f"  Home game: {away_name} at {home_name}  →  slug: {slug}")
            slugs.append(slug)
    return slugs


def launch_team(slug: str, today: str) -> tuple:
    log_dir  = f"data/nfl/{slug}"
    os.makedirs(log_dir, exist_ok=True)
    log_path = f"{log_dir}/game.log"

    log_file = open(log_path, "a")
    log_file.write(f"\n{'='*54}\n")
    log_file.write(f"  Run started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    log_file.write(f"{'='*54}\n")
    log_file.flush()

    proc = subprocess.Popen(
        [PYTHON, "nfl_run_game.py", slug, today],
        stdout=log_file,
        stderr=log_file,
    )
    return proc, log_file


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[{today}] Checking today's NFL schedule...")

    try:
        slugs = get_home_teams_espn(today)
    except Exception as e:
        print(f"Error fetching NFL schedule: {e}")
        sys.exit(1)

    if not slugs:
        print("No NFL home games today.")
        return

    print(f"\nLaunching {len(slugs)} game runner(s)...\n")

    procs = []
    for i, slug in enumerate(slugs):
        if i > 0:
            time.sleep(5)
        proc, log_file = launch_team(slug, today)
        procs.append((slug, proc, log_file))
        print(f"  [{slug}] PID {proc.pid}  →  data/nfl/{slug}/game.log")

    print(f"\nAll {len(procs)} runner(s) started. Waiting for completion...\n")

    succeeded = []
    for slug, proc, log_file in procs:
        proc.wait()
        log_file.close()
        if proc.returncode == 0:
            succeeded.append(slug)
            print(f"  [{slug}] done")
        elif proc.returncode == 2:
            print(f"  [{slug}] FAILED (Bot Detection)")
        else:
            print(f"  [{slug}] FAILED (exit {proc.returncode})")

    if succeeded:
        # Regenerate HTML story pages for ALL teams (reads from Supabase, so
        # any team with prior data will also get refreshed pages)
        print("\nRegenerating NFL story pages...")
        all_slugs = list(NFL_TEAMS.keys()) if len(succeeded) > 1 else succeeded
        for slug in all_slugs:
            try:
                subprocess.run([PYTHON, "generate_nfl_story.py", slug], check=True)
                print(f"  [{slug}] HTML regenerated")
            except Exception as e:
                print(f"  [{slug}] HTML generation failed: {e}")

        print(f"\nCommitting and pushing to GitHub...")
        date_str  = datetime.now().strftime("%Y-%m-%d")
        teams_str = ", ".join(succeeded)

        github_token = os.getenv("GITHUB_TOKEN", "").strip()
        if github_token:
            git_email = os.getenv("GIT_USER_EMAIL", "github-actions[bot]@users.noreply.github.com")
            git_name  = os.getenv("GIT_USER_NAME",  "github-actions[bot]")
            subprocess.run(["git", "config", "user.email", git_email], check=True)
            subprocess.run(["git", "config", "user.name",  git_name],  check=True)

        subprocess.run(["git", "add", "data/nfl/", "docs/"], check=True)
        subprocess.run(["git", "commit", "-m", f"NFL auto-update {date_str}: {teams_str}"], check=True)
        result = subprocess.run(["git", "push"])
        print("  Pushed." if result.returncode == 0 else "  git push failed.")

    print("\nAll done.")


if __name__ == "__main__":
    main()
