#!/usr/bin/env bash
# 持续因子研发状态：看护进程 / 最新会话日志尾 / 累计会话数 / 工作区候选数。
set -u

WORKDIR=/root/rdagent-work
PIDFILE="$WORKDIR/factor_research.pid"
LOGDIR="$WORKDIR/factor_research_logs"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "状态: RUNNING (supervisor pid $(cat "$PIDFILE"))"
else
  echo "状态: NOT RUNNING"
fi

echo "--- 看护事件（尾 5）---"
tail -n 5 "$LOGDIR/supervisor.log" 2>/dev/null || echo "(无)"

echo "--- 当前会话日志（尾 5）---"
LATEST=$(ls -t "$LOGDIR"/session-*.log 2>/dev/null | head -1)
if [ -n "${LATEST:-}" ]; then
  echo "log: $LATEST"
  tail -n 5 "$LATEST" | tr -d '\0' | cut -c1-160
else
  echo "(无会话日志)"
fi

echo "--- 累计 ---"
echo "会话数: $(ls "$LOGDIR"/session-*.log 2>/dev/null | wc -l)"
echo "工作区 factor.py 候选数: $(find "$WORKDIR/git_ignore_folder/RD-Agent_workspace" -name 'factor.py' 2>/dev/null | wc -l)"
