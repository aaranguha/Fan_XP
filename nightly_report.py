"""
nightly_report.py

Runs after all game scrapers finish (scheduled for 2 AM).
Checks what data was collected, what failed, and why.
Writes a plain-English summary to data/nightly_report.txt
so you can paste it to Claude in the morning for diagnosis.
"""

import os
import re
from datetime import datetime, date

DATA_DIR   = "data"
REPORT_OUT = "data/nightly_report.txt"


def get_game_folders():
    """Return list of (team, game_folder, game_path) for today."""
    today = date.today().isoformat()
    results = []
    for team in sorted(os.listdir(DATA_DIR)):
        team_path = os.path.join(DATA_DIR, team)
        if not os.path.isdir(team_path) or team == "nightly_report.txt":
            continue
        for folder in os.listdir(team_path):
            folder_path = os.path.join(team_path, folder)
            if os.path.isdir(folder_path) and folder.startswith(today):
                results.append((team, folder, folder_path))
    return results


def check_game(team, folder, path):
    """Return a dict summarising what happened for this game."""
    pre_csv  = os.path.join(path, "pre_game.csv")
    ht_csv   = os.path.join(path, "halftime.csv")
    ns_csv   = os.path.join(path, "no_shows.csv")

    def row_count(f):
        if not os.path.isfile(f):
            return None
        with open(f) as fh:
            return sum(1 for _ in fh) - 1  # subtract header

    return {
        "team":      team,
        "folder":    folder,
        "pre_rows":  row_count(pre_csv),
        "ht_rows":   row_count(ht_csv),
        "ns_rows":   row_count(ns_csv),
    }


def get_log_tail(team, chars=800):
    """Return the last N chars of a team's game.log for error context."""
    log = os.path.join(DATA_DIR, team, "game.log")
    if not os.path.isfile(log):
        return "(no log)"
    with open(log) as f:
        content = f.read()
    # Only the section from the last "Run started" header
    parts = content.split("=" * 54)
    return parts[-1].strip()[-chars:] if parts else content[-chars:]


def get_daily_runner_summary():
    """Extract today's block from daily_runner.log."""
    log = os.path.join(DATA_DIR, "daily_runner.log")
    if not os.path.isfile(log):
        return "(daily_runner.log not found)"
    today = date.today().isoformat()
    with open(log) as f:
        content = f.read()
    # Find the last occurrence of today's date
    idx = content.rfind(f"[{today}]")
    if idx == -1:
        return f"(no entry for {today} in daily_runner.log)"
    return content[idx:].strip()


def main():
    today     = date.today().isoformat()
    now       = datetime.now().strftime("%Y-%m-%d %H:%M")
    games     = get_game_folders()

    lines = []
    lines.append(f"FAN XP — NIGHTLY SCRAPE REPORT")
    lines.append(f"Generated: {now}")
    lines.append("=" * 60)
    lines.append("")

    # Daily runner summary
    lines.append("DAILY RUNNER OUTPUT:")
    lines.append(get_daily_runner_summary())
    lines.append("")
    lines.append("=" * 60)

    if not games:
        lines.append(f"No game folders found for {today}.")
        lines.append("Possible causes: no home games today, or all runners failed before creating folders.")
    else:
        successes = []
        failures  = []

        for team, folder, path in games:
            info = check_game(team, folder, path)
            if info["ns_rows"] is not None and info["ns_rows"] > 0:
                successes.append(info)
            else:
                failures.append((info, get_log_tail(team)))

        lines.append(f"RESULTS: {len(successes)} succeeded, {len(failures)} failed\n")

        if successes:
            lines.append("SUCCEEDED:")
            for s in successes:
                lines.append(f"  [{s['team']}] pre={s['pre_rows']} | halftime={s['ht_rows']} | no_shows={s['ns_rows']}")
            lines.append("")

        if failures:
            lines.append("FAILED — ERROR DETAILS:")
            for info, log_tail in failures:
                lines.append(f"\n  [{info['team']}] {info['folder']}")
                lines.append(f"  pre={info['pre_rows']} | halftime={info['ht_rows']} | no_shows={info['ns_rows']}")
                lines.append("  Last log output:")
                for l in log_tail.splitlines():
                    lines.append(f"    {l}")

    lines.append("")
    lines.append("=" * 60)
    lines.append("Paste this file to Claude to diagnose failures.")

    report = "\n".join(lines)

    with open(REPORT_OUT, "w") as f:
        f.write(report)

    print(report)


if __name__ == "__main__":
    main()
