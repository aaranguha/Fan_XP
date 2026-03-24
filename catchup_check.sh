#!/bin/bash
# Fires on system wake — runs daily_runner.py if it hasn't run for today yet.
# Safe to call multiple times: daily_runner.py itself skips already-scraped files.

TODAY=$(date +%Y-%m-%d)
LOG="/Users/aguha2021/Desktop/CS_Projects/Fan XP/data/daily_runner.log"

# Check if today's date already appears in the log
if grep -q "\[$TODAY\]" "$LOG" 2>/dev/null; then
    exit 0   # already ran today
fi

# Also skip if it's past 6 PM — too late for pre-game scrapes
HOUR=$(date +%H)
if [ "$HOUR" -ge 18 ]; then
    exit 0
fi

# Run via Terminal (has Full Disk Access)
osascript -e "tell application \"Terminal\" to do script \"cd '/Users/aguha2021/Desktop/CS_Projects/Fan XP' && /opt/homebrew/Caskroom/miniconda/base/bin/python3 daily_runner.py >> '/Users/aguha2021/Desktop/CS_Projects/Fan XP/data/daily_runner.log' 2>&1\""
