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
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

from mlb_teams import MLB_TEAMS

load_dotenv()

PYTHON = sys.executable


def get_home_teams_tm(today: str) -> list[tuple[str, str]]:
    """
    Fetch today's MLB home teams via the Ticketmaster Discovery API.

    Replaces the old ESPN scoreboard lookup, which started 403ing (confirmed
    live, even from this self-hosted residential IP — not just GitHub-hosted
    runners). TM's classification+date query also returns minor-league/college
    baseball, but requiring BOTH team names in an event to match one of our
    30 real MLB_TEAMS filters that out for free — no separate blocklist
    needed. A team's own keyword can also turn up in their AWAY games, so we
    take the leftmost-mentioned team as home rather than trusting the search
    keyword itself.

    Returns list of (slug, game_time_utc_iso) tuples, same shape the old
    ESPN-based function returned, so the rest of main() is unchanged.
    """
    tm_api_key = os.getenv("TICKETMASTER_API_KEY", "").strip()
    if not tm_api_key:
        raise RuntimeError("TICKETMASTER_API_KEY not set in .env")

    resp = requests.get(
        "https://app.ticketmaster.com/discovery/v2/events.json",
        params={
            "apikey":             tm_api_key,
            "classificationName": "Baseball",
            "countryCode":        "US",
            "localStartDateTime": f"{today}T00:00:00,{today}T23:59:59",
            "sort":               "date,asc",
            "size":               200,
        },
        timeout=15,
    )
    resp.raise_for_status()
    events = resp.json().get("_embedded", {}).get("events", [])

    results = []
    for event in events:
        name = event.get("name", "")
        name_lower = name.lower()

        matches = []  # (index, slug)
        for slug, team in MLB_TEAMS.items():
            idx = name_lower.find(team["tm_keyword"].lower())
            if idx >= 0:
                matches.append((idx, slug))
        if len(matches) != 2:
            continue  # not a real MLB-vs-MLB matchup (minor league, tours, etc.)

        matches.sort()
        home_slug = matches[0][1]
        game_time = event.get("dates", {}).get("start", {}).get("dateTime", "")
        print(f"  Home game found: {name}  →  slug: {home_slug}  time: {game_time}")
        results.append((home_slug, game_time))
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
    # Allow date override via CLI arg or environment variable (for testing)
    if len(sys.argv) > 1:
        today = sys.argv[1]
    else:
        today = os.getenv("MLB_DATE") or datetime.now().strftime("%Y-%m-%d")
    print(f"[{today}] Checking today's MLB schedule...")

    try:
        games = get_home_teams_tm(today)
    except Exception as e:
        print(f"Error fetching MLB schedule: {e}")
        sys.exit(1)

    if not games:
        print("No MLB home games today.")
        return

    # Only launch games whose pre-game scrape window is within the next 6 hours.
    # This keeps GitHub Actions jobs well within the 6-hour timeout.
    PRE_GAME_OFFSET_MIN = 60
    MAX_WAIT_MIN        = 120  # 2 hours — pre-game must be within this window
    now_utc = datetime.now(timezone.utc)
    eligible = []
    for slug, game_time_str in games:
        if not game_time_str:
            eligible.append(slug)
            continue
        try:
            game_utc  = datetime.fromisoformat(game_time_str.replace("Z", "+00:00"))
            pregame   = game_utc - timedelta(minutes=PRE_GAME_OFFSET_MIN)
            wait_min  = (pregame - now_utc).total_seconds() / 60
            if wait_min <= MAX_WAIT_MIN:
                eligible.append(slug)
            else:
                print(f"  Skipping {slug} — pre-game in {wait_min:.0f} min (next cron will catch it)")
        except Exception:
            eligible.append(slug)

    if not eligible:
        print("No games within the next 6 hours — next cron will handle them.")
        return

    slugs = eligible
    print(f"\nLaunching {len(slugs)} game runner(s)...\n")

    # Stagger launches by 90s so browsers don't all hit TM simultaneously
    LAUNCH_STAGGER_SEC = 90
    procs = []
    for i, slug in enumerate(slugs):
        if i > 0:
            print(f"  Waiting {LAUNCH_STAGGER_SEC}s before next launch...")
            time.sleep(LAUNCH_STAGGER_SEC)
        proc, log_file = launch_team(slug, today)
        procs.append((slug, proc, log_file))
        print(f"  [{slug}] PID {proc.pid}  →  data/mlb/{slug}/game.log")

    print(f"\nAll {len(procs)} runner(s) started. Waiting for games to complete...\n")
    sys.stdout.flush()

    succeeded = []
    last_lines = {}
    for slug, proc, log_file in procs:
        # Poll every 60s; only print when the last log line actually changes
        while proc.poll() is None:
            time.sleep(60)
            log_path = f"data/mlb/{slug}/game.log"
            if os.path.isfile(log_path):
                try:
                    with open(log_path) as lf:
                        lines = lf.readlines()
                    last = next((l.rstrip() for l in reversed(lines) if l.strip()), "")
                    if last and last != last_lines.get(slug):
                        last_lines[slug] = last
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
        # Regenerate HTML story pages for ALL teams (reads from Supabase, so
        # any team with prior data will also get refreshed pages)
        print("\nRegenerating MLB story pages...")
        all_slugs = list(MLB_TEAMS.keys()) if len(succeeded) > 1 else succeeded
        for slug in all_slugs:
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

        subprocess.run(["git", "add", "data/mlb/", "../docs/"], check=True)
        commit = subprocess.run([
            "git", "commit", "-m", f"MLB auto-update {date_str}: {teams_str}"
        ])
        if commit.returncode == 0:
            subprocess.run(["git", "pull", "--rebase"], check=False)
            result = subprocess.run(["git", "push"])
            if result.returncode == 0:
                print("  Pushed to GitHub.")
            else:
                print("  git push failed — check SSH keys / remote access.")
        else:
            print("  Nothing new to commit.")

    print("\nAll done.")


if __name__ == "__main__":
    main()
