#!/usr/bin/env bash
# Cron wrapper for daily_digest.py.
#
# Cron runs with a minimal PATH and no .zshrc/.bashrc loaded — this script
# rebuilds enough environment that `uv run` can find Python + dependencies,
# then invokes the digest generator. Output goes to ~/.tradingagents/digest.log
# so cron failures stay visible without spamming /tmp.
#
# Install:
#   crontab -e
# Add (5pm Mon-Fri, after market close 4pm ET → 1pm PT, give yfinance buffer):
#   0 17 * * 1-5  /Users/jyang/projects/TradingAgents/scripts/run_daily_digest.sh
#
# Logs:
#   tail -f ~/.tradingagents/digest.log
#   ls outputs/digest_*.md

set -e

# uv is installed under ~/.local/bin; cron's PATH won't have it.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$HOME/.tradingagents"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/digest.log"

{
  echo
  echo "=== $(date '+%Y-%m-%d %H:%M:%S') ==="
  cd "$REPO"
  uv run python scripts/daily_digest.py 2>&1
} >> "$LOG" 2>&1
