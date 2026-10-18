#!/usr/bin/env bash
# 启动持续因子研发（delta add-factor-research-control：经研发代理调和；单一意图源）。
#
# 语义（看护本体不变：无界 fin_factor 循环 + 崩溃退避自愈，由代理写/管的看护脚本承载）：
#   - 确保研发代理守护存活（观测 + 控制合一；看护停止也不退出，见 factor_research_agent.py）；
#   - 写期望态 desired_running=true（与界面开关共用同一条控制文件——手动启停也走意图源，
#     代理调和不会把手动停止「扳回去」）；
#   - 立即触发一次调和（不必等代理轮询周期）并校验看护已起。
#
# 启停：start-factor-research.sh / stop-factor-research.sh / status-factor-research.sh
set -u

WORKDIR=/root/rdagent-work
AGENT=/root/factor_research_agent.py
CONTROL=/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/control.json
PIDFILE="$WORKDIR/factor_research.pid"

if [ ! -f "$AGENT" ]; then
  echo "FAILED: 研发代理未部署（$AGENT 不存在——见 tests/scripts/spike/README.md 部署步骤）"
  exit 1
fi

python3 "$AGENT" --workdir "$WORKDIR" --control "$CONTROL" --ensure-daemon || {
  echo "FAILED: 代理守护无法启动（见 $WORKDIR/factor_research_logs/agent.log）"
  exit 1
}
python3 "$AGENT" --workdir "$WORKDIR" --control "$CONTROL" --set-intent running
python3 "$AGENT" --workdir "$WORKDIR" --control "$CONTROL" --reconcile-once || {
  echo "FAILED: 调和未成功（见 $WORKDIR/factor_research_logs/agent.log）"
  exit 1
}

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "started supervisor pid $(cat "$PIDFILE")（代理守护承接观测与调和）"
else
  echo "FAILED to start supervisor"
  exit 1
fi
