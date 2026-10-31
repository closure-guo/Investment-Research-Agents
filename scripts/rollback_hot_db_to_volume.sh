#!/usr/bin/env bash
# 热库回滚：named volume finance-agent_data → 宿主 data/（harden-sqlite-availability，issue #515）
#
# 场景：迁移后发现卷内数据异常，退回 bind mount 形态。
# 步骤：停后端 → 卷→宿主 拷贝（写到 data/rollback-output-<ts>/，不覆盖原副本）
#       → 人工核对 → 还原 docker-compose.yml（git checkout）→ 起栈。
# 宿主 data/ 的迁移前副本全程未被删除，若它完好可直接跳过拷贝走「还原 compose」。

set -euo pipefail
cd "$(dirname "$0")/.."

VOL="finance-agent_data"
IMG="python:3-alpine"
OUT="data/rollback-output-$(date +%Y%m%d-%H%M%S)"

phase() { echo; echo "════════ $1 ════════"; }

phase "前置检查"
if curl -sf --max-time 3 http://localhost:8000/api/health >/dev/null 2>&1; then
  n=$(curl -sf http://localhost:8000/api/sessions | grep -o '"status":"running"' | wc -l)
  [ "$n" = "0" ] || { echo "有 $n 个 running 会话，先等完成或确认"; exit 1; }
fi
docker compose stop backend

phase "卷 → 宿主（$OUT）"
mkdir -p "$OUT"
docker run --rm -v "$VOL":/src:ro -v "$PWD/$OUT":/dst $IMG sh -c 'cp -a /src/. /dst/ && echo "回拷完成：$(ls /dst | wc -l) 项"'

phase "对账（volume vs 回拷）"
docker run --rm -v "$VOL":/src:ro -v "$PWD/$OUT":/dst $IMG python - <<'PYEOF'
import sqlite3, sys, os
dbs = ["sessions.db", "screening.db", "execution.db", "events.db",
       "regime.db", "research_reports.db", "factor_library.db", "market_snapshot.db"]
fail = 0
for db in dbs:
    s, d = f"/src/{db}", os.path.join("/dst", db)
    if not os.path.exists(s) or not os.path.exists(d):
        print(f"  [SKIP] {db}（缺失）"); continue
    cs, cd = sqlite3.connect(s), sqlite3.connect(d)
    tables = [r[0] for r in cs.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    bad = [t for t in tables
           if cs.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
           != cd.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]]
    print(f"  [{'FAIL' if bad else 'OK'}] {db}" + (f" 行数不一致: {bad[:3]}" if bad else ""))
    fail = fail or bool(bad)
sys.exit(fail)
PYEOF

phase "后续手工步骤（不自动化，防误覆盖）"
cat <<'EOF'
1. 核对 $OUT 内库文件后，自行决定覆盖方式：
   - 仅验证卷内容可读：无需覆盖，直接还原 compose 起栈即可（宿主 data/ 原副本仍在）
   - 需要以卷内数据覆盖宿主：cp data/rollback-output-<ts>/*.db data/
2. 还原 compose：git checkout docker-compose.yml
3. 起栈：docker compose up -d backend
4. 验证：/api/health、/api/sessions、/api/factors、/api/snapshot/status
EOF
