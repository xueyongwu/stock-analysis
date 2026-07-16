#!/usr/bin/env bash
# 每日增量更新 A股中位数数据 + 重新导出 data.js。给 crontab 调用。
# 非交易日脚本内部自动跳过(median_trend.py --update 有交易日守卫)。
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

LOG="$DIR/cache/cron.log"
mkdir -p "$DIR/cache"

echo "===== $(date '+%F %T') update start =====" >> "$LOG"
"$DIR/.venv/bin/python" -u median_trend.py --update >> "$LOG" 2>&1
echo "===== $(date '+%F %T') update done =====" >> "$LOG"
