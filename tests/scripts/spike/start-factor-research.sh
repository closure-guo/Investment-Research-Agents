#!/usr/bin/env bash
# 持续因子研发看护（spike）：无界 fin_factor 循环 + 崩溃自动重启，直到显式 stop。
#
# 语义：
#   - 每个会话 = 一次 `rdagent fin_factor`（--loop-n 不设 = 无界循环）；
#   - 会话异常退出（崩溃/配额耗尽）→ 自动拉起新会话（短命会话 <120s → 退避 10min，
#     正常结束/长会话 → 60s 后继续）；
#   - setsid 脱离会话，宿主侧 wsl 命令退出/我的会话结束均不影响；
#   - 日志：/root/rdagent-work/factor_research_logs/session-<ts>.log（逐会话）+
#     supervisor.log（看护事件）+ collector.log（采集器）。
#   - 同时拉起状态采集器（collect_factor_status.py，add-factor-research-monitor）：
#     只读观测，周期写 JSON 快照到宿主 data/factor_research/status.json 供监控页读取。
#
# 启停：start-factor-research.sh / stop-factor-research.sh / status-factor-research.sh
set -u

WORKDIR=/root/rdagent-work
PIDFILE="$WORKDIR/factor_research.pid"
LOGDIR="$WORKDIR/factor_research_logs"
mkdir -p "$LOGDIR"

if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "already running (supervisor pid $(cat "$PIDFILE"))"
  exit 0
fi

cat > "$WORKDIR/.factor_research_supervisor.sh" <<'SUPERVISOR'
#!/usr/bin/env bash
set -u
WORKDIR=/root/rdagent-work
LOGDIR="$WORKDIR/factor_research_logs"
echo $$ > "$WORKDIR/factor_research.pid"
echo "[supervisor] started at $(date -Is) pid $$" >> "$LOGDIR/supervisor.log"
while true; do
  LOG="$LOGDIR/session-$(date +%Y%m%d-%H%M%S).log"
  START=$(date +%s)
  bash /root/launch-fin-factor.sh > "$LOG" 2>&1
  END=$(date +%s)
  DUR=$((END - START))
  echo "[supervisor] session ended after ${DUR}s (log: $LOG) at $(date -Is)" >> "$LOGDIR/supervisor.log"
  if [ "$DUR" -lt 120 ]; then
    sleep 600   # 短命会话（配额/网络故障）→ 退避 10 分钟
  else
    sleep 60
  fi
done
SUPERVISOR

chmod +x "$WORKDIR/.factor_research_supervisor.sh"
nohup setsid bash "$WORKDIR/.factor_research_supervisor.sh" > /dev/null 2>&1 &
sleep 2
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "started supervisor pid $(cat "$PIDFILE")"
else
  echo "FAILED to start supervisor"; exit 1
fi

# 状态采集器（add-factor-research-monitor）：只读观测，周期写快照到宿主 data/（/mnt/d 桥）。
# 采集器失败不阻断研发（监控页显示「未接入/陈旧」如实降级）。
COLLECTOR_PIDFILE="$WORKDIR/factor_status_collector.pid"
if [ -f "$COLLECTOR_PIDFILE" ] && kill -0 "$(cat "$COLLECTOR_PIDFILE")" 2>/dev/null; then
  echo "collector already running (pid $(cat "$COLLECTOR_PIDFILE"))"
else
  rm -f "$COLLECTOR_PIDFILE"
  setsid nohup python3 /root/collect_factor_status.py \
    --workdir "$WORKDIR" --pidfile "$COLLECTOR_PIDFILE" \
    >> "$LOGDIR/collector.log" 2>&1 &
  sleep 1
  if [ -f "$COLLECTOR_PIDFILE" ] && kill -0 "$(cat "$COLLECTOR_PIDFILE")" 2>/dev/null; then
    echo "started status collector pid $(cat "$COLLECTOR_PIDFILE")"
  else
    echo "WARN: status collector failed to start (monitor page will show missing/stale)"
  fi
fi
