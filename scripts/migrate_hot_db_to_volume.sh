#!/usr/bin/env bash
# 热库迁移：宿主 data/ → named volume finance-agent_data（harden-sqlite-availability，issue #515）
#
# 背景：data/ 位于 Windows bind mount（9p），跨挂载 WAL 并发已四次打穿生产
#（incident 043/044/046 + 053 的 136s 连接全灭）。SQLite 族整体入卷后宿主机
# 不再直碰热库（BREAKING 部署路径；备份/排障走 docker exec）。
#
# 用法（仓库根执行，Git Bash）：
#   bash scripts/migrate_hot_db_to_volume.sh              # 全程：前置检查→停后端→整拷→对账→起栈→端点验证
#   bash scripts/migrate_hot_db_to_volume.sh --verify-only # 只对账（不停止/不写入）
#   bash scripts/migrate_hot_db_to_volume.sh --copy-only   # 只做前置+停+拷+对账（不起栈，供手工核验后再起）
#
# 回滚：bash scripts/rollback_hot_db_to_volume.sh（volume → 宿主 data/，另需还原 compose）
# 回滚副本：宿主 data/ 全程原样保留，迁移不做任何删除。

set -euo pipefail
cd "$(dirname "$0")/.."

# Docker Desktop 的 -v 只认 Windows 形态路径：pwd -W 输出 D:/...（MSYS 的
# /d/... 会让容器侧挂载出空目录——2026-11-01 首跑实证，拷贝静默落空）
WD="$(pwd -W)"
MODE="${1:-migrate}"
VOL="finance-agent_data"
HOT_DBS=(sessions.db screening.db execution.db events.db regime.db research_reports.db factor_library.db market_snapshot.db)
IMG="python:3-alpine"

phase() { echo; echo "════════ $1 ════════"; }

api_running_count() {
  local url="$1"
  # grep 零命中（=0 个 running，正是期望态）在 pipefail 下返回 1——用 || true
  # 吸收，避免调用方 `|| echo 0` 兜底拼出 "00"；tr：Git Bash 的 wc -l 带前导空格
  curl -sf --max-time 10 "$url" | { grep -o '"status":"running"' || true; } | wc -l | tr -d '[:space:]'
}

require_backend_down() {
  if curl -sf --max-time 3 http://localhost:8000/api/health >/dev/null 2>&1; then
    echo "后端仍在运行——迁移要求先停栈（docker compose stop backend）"; exit 1
  fi
}

verify_pair() {
  # -i：heredoc 经 stdin 喂 python，缺 -i 时容器 stdin 非 TTY 直接空转——
  # 首跑实证对账整段静默跳过（exit 0 放行），验证必须带 -i。
  # src/dst 在 heredoc 内硬编码：MSYS 会把 POSIX 形态的命令行参数转换成
  # Git 安装路径（/src → C:\Program Files\Git\src），传参形态下挂载明明
  # 正常、python 却找不到目录（三坑叠加的最终真相，2026-11-01 实证）
  docker run --rm -i -v "$WD/data":/src:ro -v "$VOL":/dst $IMG python - <<'PYEOF' 
import sqlite3, os
src, dst = "/src", "/dst"
dbs = ["sessions.db", "screening.db", "execution.db", "events.db",
       "regime.db", "research_reports.db", "factor_library.db", "market_snapshot.db"]
fail = 0
for db in dbs:
    s, d = os.path.join(src, db), os.path.join(dst, db)
    # 源侧连 -wal/-shm 拷进可写 /tmp 再开（9p 只读 + WAL 恢复要建 shm，直开必炸）
    import glob, shutil
    for f in glob.glob(f"{s}*"):
        shutil.copy(f, "/tmp/" + os.path.basename(f))
    s = "/tmp/" + db
    if not os.path.exists(s):
        print(f"  [FAIL] {db}: 源缺失（挂载路径错误？宿主副本必须存在）"); fail = 1; continue
    if not os.path.exists(d):
        print(f"  [FAIL] {db}: 卷内缺失"); fail = 1; continue
    cs = sqlite3.connect(s); cd = sqlite3.connect(d)
    qs = cs.execute("PRAGMA quick_check").fetchone()[0]
    qd = cd.execute("PRAGMA quick_check").fetchone()[0]
    if qs != "ok" or qd != "ok":
        print(f"  [FAIL] {db}: quick_check src={qs} dst={qd}"); fail = 1; continue
    tables = [r[0] for r in cs.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    mismatch = []
    for t in tables:
        n_s = cs.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0]
        n_d = cd.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0]
        if n_s != n_d:
            mismatch.append(f"{t}:{n_s}!={n_d}")
    size_s, size_d = os.path.getsize(s), os.path.getsize(d)
    # 尺寸允许 ±4KB 内差异（checkpoint 后页对齐）；行数必须逐表一致
    if mismatch:
        print(f"  [FAIL] {db}: 行数不一致 {'; '.join(mismatch[:4])}"); fail = 1
    elif abs(size_s - size_d) > 4096:
        print(f"  [FAIL] {db}: 尺寸差过大 src={size_s} dst={size_d}"); fail = 1
    else:
        print(f"  [OK] {db}: quick_check ok，{len(tables)} 表行数逐一致（src {size_s//1024//1024}MB）")
    cs.close(); cd.close()
exit(fail)
PYEOF
}

phase "前置检查（running 会话/作业 = 0）"
if curl -sf --max-time 3 http://localhost:8000/api/health >/dev/null 2>&1; then
  s_cnt=$(api_running_count "http://localhost:8000/api/sessions")
  j_cnt=$(api_running_count "http://localhost:8000/api/v1/ops/jobs")
  echo "running 会话=$s_cnt running 作业=$j_cnt"
  [ "$s_cnt" = "0" ] && [ "$j_cnt" = "0" ] || { echo "存在 running，中止（等分析完成或经用户确认）"; exit 1; }
else
  echo "后端当前不可达（已停栈形态？）——继续（复制阶段要求停栈）"
fi

if [ "$MODE" = "--verify-only" ]; then
  phase "对账（仅验证，源=宿主 data/ vs 卷）"
  verify_pair /src /dst
  echo "verify-only 完成"; exit 0
fi

phase "停后端"
docker compose stop backend

phase "整拷 data/ → volume（含校验，排除测试/备份残渣）"
docker run --rm -v "$WD/data":/src:ro -v "$VOL":/dst $IMG sh -c '
  cd /src && find . -maxdepth 1 \
    ! -name "." \
    ! -name "test-e2e-*" ! -name "*.bak-*" ! -name "*-bak*" \
    ! -name "*.corrupt-*" ! -name "repro-stub.db" \
    ! -name "*.db-wal" ! -name "*.db-shm" \
    -exec cp -a {} /dst/ \;
  n=$(ls /dst | wc -l)
  echo "拷贝完成：$n 项"
  for db in sessions.db screening.db market_snapshot.db; do
    [ -f "/dst/$db" ] || { echo "致命：$db 未出现在卷内"; exit 1; }
  done
  echo "关键库在卷校验通过"
'

phase "对账（quick_check + 全表行数 + 尺寸）"
verify_pair /src /dst

if [ "$MODE" = "--copy-only" ]; then
  phase "copy-only 完成（后端保持停止；核验后 docker compose up -d backend）"
  exit 0
fi

phase "起栈（新 compose：named volume + 三个 JSON 子目录 bind）"
docker compose up -d backend
echo "等待 health…"
for i in $(seq 1 40); do
  curl -sf --max-time 3 http://localhost:8000/api/health >/dev/null 2>&1 && break
  sleep 3
done
curl -sf http://localhost:8000/api/health || { echo "health 未就绪，检查 docker compose logs backend"; exit 1; }

phase "端点验证（读卷）"
echo "-- /api/sessions 最新 1 条："
curl -sf "http://localhost:8000/api/sessions" | python -c "import json,sys; d=json.load(sys.stdin); s=d['sessions'][0]; print('  ', s['created_at'], s['status'], s['display_name'][:40])"
echo "-- /api/factors 因子数："
curl -sf "http://localhost:8000/api/factors" | python -c "import json,sys; d=json.load(sys.stdin); print('  ', len(d.get('factors', d if isinstance(d,list) else [])), '条')"
echo "-- /api/snapshot/status："
curl -sf "http://localhost:8000/api/snapshot/status" | head -c 200; echo

phase "迁移完成"
echo "宿主 data/ 原样保留为回滚副本；如需回退：bash scripts/rollback_hot_db_to_volume.sh"
