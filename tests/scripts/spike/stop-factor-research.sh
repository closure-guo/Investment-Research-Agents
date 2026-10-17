#!/usr/bin/env bash
# 停止持续因子研发（看护进程 + 当前 fin_factor 会话）。
#
# delta add-factor-research-control：先经研发代理调和（写期望态 stopped → 立即调和），
# 与界面开关共用同一条意图源；保留 bash 兜底硬停（代理不可用时仍停得掉）。
set -u

WORKDIR=/root/rdagent-work
AGENT=/root/factor_research_agent.py
CONTROL=/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/control.json
PIDFILE="$WORKDIR/factor_research.pid"

if [ -f "$AGENT" ]; then
  python3 "$AGENT" --workdir "$WORKDIR" --control "$CONTROL" --set-intent stopped
  python3 "$AGENT" --workdir "$WORKDIR" --control "$CONTROL" --reconcile-once || true
fi

# 兜底硬停（代理不可用 / 调和未清干净时保持旧脚本逐字行为）
if [ -f "$PIDFILE" ]; then
  PID=$(cat "$PIDFILE")
  pkill -P "$PID" 2>/dev/null || true   # 当前会话（直接子进程）
  kill "$PID" 2>/dev/null || true
  rm -f "$PIDFILE"
  echo "stopped supervisor $PID (hard fallback)"
fi

# 兜底：清理残留 fin_factor 进程
pkill -f fin_factor 2>/dev/null || true
sleep 1
if pgrep -f fin_factor > /dev/null 2>&1; then
  echo "WARN: fin_factor process still present"
else
  echo "all clear"
fi

# 兜底写末次快照（代理守护未运行时保证页面即时反映停止；守护在跑时下一周期自写）；
# 代理在跑时不重复写（避免与其采集周期争用增量状态文件）。
AGENT_PIDFILE="$WORKDIR/factor_research_agent.pid"
if [ -f "$AGENT_PIDFILE" ] && kill -0 "$(cat "$AGENT_PIDFILE")" 2>/dev/null; then
  echo "agent daemon alive（末次快照由代理下一周期自写）"
else
  if python3 /root/collect_factor_status.py --workdir "$WORKDIR" --once 2>/dev/null; then
    echo "final status snapshot written"
  else
    echo "WARN: final status snapshot failed"
  fi
fi
