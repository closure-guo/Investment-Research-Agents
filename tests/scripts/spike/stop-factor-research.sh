#!/usr/bin/env bash
# 停止持续因子研发（看护进程 + 当前 fin_factor 会话）。
set -u

WORKDIR=/root/rdagent-work
PIDFILE="$WORKDIR/factor_research.pid"

if [ -f "$PIDFILE" ]; then
  PID=$(cat "$PIDFILE")
  pkill -P "$PID" 2>/dev/null || true   # 当前会话（直接子进程）
  kill "$PID" 2>/dev/null || true
  rm -f "$PIDFILE"
  echo "stopped supervisor $PID"
else
  echo "no pidfile (supervisor not running?)"
fi

# 兜底：清理残留 fin_factor 进程
pkill -f fin_factor 2>/dev/null || true
sleep 1
if pgrep -f fin_factor > /dev/null 2>&1; then
  echo "WARN: fin_factor process still present"
else
  echo "all clear"
fi
