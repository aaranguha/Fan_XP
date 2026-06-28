"""
mlb_runner.py

Checks today's MLB schedule and launches mlb_run_game.py for each team
with a home game today. Mirrors daily_runner.py for NBA.

Usage:
    python mlb_runner.py

Cron setup (runs at 9 AM local time every day during MLB season):
    0 9 * * * cd "/path/to/Fan XP" && python3 mlb_runner.py >> data/mlb_runner.log 2>&1
"""

import os
import subprocess
import sys
import time
from datetime import datetime

import requests

from mlb_teams import MLB_TRICODE_TO_SLUG, MLB_TEAMS

PYTHON = sys.executable


def get_home_teams_espn(today: str) -> list[tuple[str, str]]:
    """
    Fetch today's MLB home teams from ESPN scoreboard API.
    Returns list of (slug, game_time_utc) tuples.
    """
    date_str = today.replace("-", "")
    url = f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard?dates={date_str}"
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    resp.raise_for_status()

    results = []
    for event in resp.json().get("events", []):
        comps = event["competitions"][0]
        home = next((t for t in comps["competitors"] if t["homeAway"] == "home"), None)
        away = next((t for t in comps["competitors"] if t["homeAway"] == "away"), None)
        if not home:
            continue
        tricode = home["team"]["abbreviation"]
        slug = MLB_TRICODE_TO_SLUG.get(tricode)
        if slug:
            game_time = event.get("date", "")
            home_name = home["team"]["displayName"]
            away_name = away["team"]["displayName"] if away else "?"
            print(f"  Home game found: {away_name} at {home_name}  →  slug: {slug}  time: {game_time}")
            results.append((slug, game_time))
    return results


def launch_team(slug: str, today: str) -> subprocess.Popen:
    log_dir  = f"data/mlb/{slug}"
    os.makedirs(log_dir, exist_ok=True)
    log_path = f"{log_dir}/game.log"

    log_file = open(log_path, "a")
    log_file.write(f"\n{'='*54}\n")
    log_file.write(f"  Run started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    log_file.write(f"{'='*54}\n")
    log_file.flush()

    proc = subprocess.Popen(
        [PYTHON, "mlb_run_game.py", slug, today],
        stdout=log_file,
        stderr=log_file,
    )
    return proc, log_file


def main():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[{today}] Checking today's MLB schedule...")

    try:
        games = get_home_teams_espn(today)
    except Exception as e:
        print(f"Error fetching MLB schedule: {e}")
        sys.exit(1)

    if not games:
        print("No MLB home games today.")
        return

    slugs = [slug for slug, _ in games]
    print(f"\nLaunching {len(slugs)} game runner(s)...\n")

    procs = []
    for i, slug in enumerate(slugs):
        if i > 0:
            time.sleep(5)
        proc, log_file = launch_team(slug, today)
        procs.append((slug, proc, log_file))
        print(f"  [{slug}] PID {proc.pid}  →  data/mlb/{slug}/game.log")

    print(f"\nAll {len(procs)} runner(s) started. Waiting for games to complete...\n")
    sys.stdout.flush()

    succeeded = []
    for slug, proc, log_file in procs:
        # Poll every 60s so GitHub Actions shows we're alive
        while proc.poll() is None:
            time.sleep(60)
            log_path = f"data/mlb/{slug}/game.log"
            if os.path.isfile(log_path):
                try:
                    with open(log_path) as lf:
                        lines = lf.readlines()
                    # Print last non-empty line as heartbeat
                    last = next((l.rstrip() for l in reversed(lines) if l.strip()), "")
                    if last:
                        print(f"  [{slug}] {last}", flush=True)
                except Exception:
                    pass
        log_file.close()
        if proc.returncode == 0:
            succeeded.append(slug)
            print(f"  [{slug}] ✓ done", flush=True)
        elif proc.returncode == 2:
            print(f"  [{slug}] ✗ FAILED (Bot Detection)", flush=True)
        else:
            print(f"  [{slug}] ✗ FAILED (exit {proc.returncode})", flush=True)

    if succeeded:
        # Regenerate HTML story pages for teams that completed
        print("\nRegenerating MLB story pages...")
        for slug in succeeded:
            try:
                subprocess.run([PYTHON, "generate_mlb_story.py", slug], check=True)
                print(f"  [{slug}] HTML regenerated")
            except Exception as e:
                print(f"  [{slug}] HTML generation failed: {e}")

        print(f"\nCommitting and pushing to GitHub (triggers Vercel deploy)...")
        date_str = datetime.now().strftime("%Y-%m-%d")
        teams_str = ", ".join(succeeded)

        github_token = os.getenv("GITHUB_TOKEN", "").strip()
        if github_token:
            git_email = os.getenv("GIT_USER_EMAIL", "bot@fanxp.com")
            git_name  = os.getenv("GIT_USER_NAME", "FanXP Bot")
            subprocess.run(["git", "config", "user.email", git_email], check=True)
            subprocess.run(["git", "config", "user.name",  git_name],  check=True)
            raw_url = subprocess.check_output(["git", "remote", "get-url", "origin"]).decode().strip()
            if "github.com" in raw_url and f"{github_token}@" not in raw_url:
                if raw_url.startswith("git@github.com:"):
                    path = raw_url.replace("git@github.com:", "").rstrip(".git")
                else:
                    path = raw_url.split("github.com/", 1)[-1].rstrip(".git")
                auth_url = f"https://{github_token}@github.com/{path}.git"
                subprocess.run(["git", "remote", "set-url", "origin", auth_url], check=True)

        subprocess.run(["git", "add", "data/mlb/", "docs/"], check=True)
        subprocess.run([
            "git", "commit", "-m", f"MLB auto-update {date_str}: {teams_str}"
        ], check=True)
        result = subprocess.run(["git", "push"])
        if result.returncode == 0:
            print("  Pushed to GitHub.")
        else:
            print("  git push failed — check SSH keys / remote access.")

    print("\nAll done.")


if __name__ == "__main__":
    main()
