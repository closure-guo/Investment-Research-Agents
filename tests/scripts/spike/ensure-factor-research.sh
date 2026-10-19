#!/usr/bin/env bash
# 研发看护自愈启动器入口（delta add-factor-research-selfheal）。
#
# 幂等节拍：确保研发代理守护存活 + 写 launcher.json 心跳。
# MUST NOT 写控制文件（用户意图唯一写入面）与直接启停看护（只由代理按期望态调和）。
set -u

WORKDIR=/root/rdagent-work
AGENT=/root/factor_research_agent.py
CONTROL=/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/control.json
LAUNCHER=${FACTOR_RESEARCH_LAUNCHER_PATH:-/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/launcher.json}

if [ ! -f "$AGENT" ]; then
  echo "FAILED: 研发代理未部署（$AGENT 不存在）" >&2
  exit 1
fi

exec python3 "$AGENT" --launcher-tick \
  --workdir "$WORKDIR" \
  --control "$CONTROL" \
  --launcher "$LAUNCHER"
