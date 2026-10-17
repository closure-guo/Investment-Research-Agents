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

# 状态采集器收尾（add-factor-research-monitor）：先停守护（防状态文件双写竞态），
# 再 --once 写末次快照（supervisor.running=false，监控页即时反映停止）。
COLLECTOR_PIDFILE="$WORKDIR/factor_status_collector.pid"
if [ -f "$COLLECTOR_PIDFILE" ]; then
  kill "$(cat "$COLLECTOR_PIDFILE")" 2>/dev/null || true
  rm -f "$COLLECTOR_PIDFILE"
  echo "stopped status collector"
fi
if python3 /root/collect_factor_status.py --workdir "$WORKDIR" --once 2>/dev/null; then
  echo "final status snapshot written"
else
  echo "WARN: final status snapshot failed"
fi
