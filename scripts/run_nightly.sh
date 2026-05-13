#!/usr/bin/env bash
# Nightly portfolio analysis — invoked by cron, Sun-Thu 21:00 local time.
# trade-date = today, except Sunday rolls back to Friday (last market close).
set -euo pipefail

cd "$(dirname "$0")/.."

dow=$(date +%u)  # 1=Mon ... 7=Sun
if [ "$dow" = "7" ]; then
  trade_date=$(date -v-2d +%Y-%m-%d)
else
  trade_date=$(date +%Y-%m-%d)
fi

LOG_DIR="$HOME/Library/Logs/tradingagents"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/nightly-$(date +%Y%m%d).log"

echo "[$(date -Iseconds)] Starting nightly run for trade_date=$trade_date" >> "$LOG_FILE"
/Users/jyang/.local/bin/uv run python scripts/analyze_holdings.py \
  --all --min-pct 0.3 --trade-date "$trade_date" \
  >> "$LOG_FILE" 2>&1

echo "[$(date -Iseconds)] Migrating decisions into DB" >> "$LOG_FILE"
exec /Users/jyang/.local/bin/uv run python scripts/migrate_decisions_to_db.py \
  >> "$LOG_FILE" 2>&1
