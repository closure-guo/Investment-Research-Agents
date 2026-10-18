#!/usr/bin/env bash
# 持续因子研发状态：看护进程 / 研发代理 / 控制文件期望态 / 最新会话日志尾 / 累计计数。
set -u

WORKDIR=/root/rdagent-work
PIDFILE="$WORKDIR/factor_research.pid"
AGENT_PIDFILE="$WORKDIR/factor_research_agent.pid"
CONTROL=/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/control.json
LOGDIR="$WORKDIR/factor_research_logs"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "状态: RUNNING (supervisor pid $(cat "$PIDFILE"))"
else
  echo "状态: NOT RUNNING"
fi

# 研发代理（delta add-factor-research-control：观测 + 控制合一，独立于看护存活）
if [ -f "$AGENT_PIDFILE" ] && kill -0 "$(cat "$AGENT_PIDFILE")" 2>/dev/null; then
  echo "代理: RUNNING (pid $(cat "$AGENT_PIDFILE"))"
else
  echo "代理: NOT RUNNING（控制通道失联——start 脚本会重新拉起）"
fi

if [ -f "$WORKDIR/factor_status_collector.pid" ] && kill -0 "$(cat "$WORKDIR/factor_status_collector.pid")" 2>/dev/null; then
  echo "遗留采集器: RUNNING（旧守护未清理——stop 脚本会收掉）"
fi

echo "--- 控制文件（期望态）---"
cat "$CONTROL" 2>/dev/null || echo "(无控制文件——代理不干预看护启停)"

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
