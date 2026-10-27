# 收割因子分桶 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 收割入库 category 从硬编码「外部」改为确定性关键词映射到五桶（量与流动性/波动率/动量反转/量价交互/外部-其他），分桶保种对外部链路生效；存量 12 条幂等迁移。

**Architecture:** `harvest.py` 模块级有序关键词表（机理标签优先、因子名次之，首中即停；全部未命中落兜底桶「外部-其他」）+ `classify_category(name, description) -> str`；三处 payload 构造点（转译失败/不可翻译/过闸）统一改走映射；存量迁移脚本对 `category="外部"` 行按同一函数重写并落 `factor_events` 迁移事件。

**Tech Stack:** Python 3.14 / sqlite3 / pytest

## Global Constraints

- 分类只决定桶归属，MUST NOT 影响准入路径（分类异常不得让收割批失败）
- 映射为纯函数确定性匹配（零 LLM 成本）；映射表随产物留存可审计
- `source="external"` 语义不变；五道闸、闸④冗余闸零改动
- 存量历史 `BUCKET_QUOTA` 事件不改写（不静默改写历史）
- 测试落 tmp_path 零网络；对存量 12 条 external 因子的期望桶归属钉死

---

### Task 1: 分类函数（有序关键词表）

**Files:**
- Modify: `src/finance_agent/factors/harvest.py`（`DEFAULT_CATEGORY` 常量区附近）
- Test: `tests/factors/test_harvest.py`

**Interfaces:**
- Consumes: 无
- Produces: `CATEGORY_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...]`（有序）；`classify_category(name: str, description: str) -> str`；常量 `CATEGORY_FALLBACK = "外部-其他"`

- [ ] **Step 1: Write the failing test**

存量 12 条 external 因子的真实标签锚定期望桶（标签来自生产库 hypothesis 首段，实测校准）：

```python
class TestClassifyCategory:
    # 锚定集：RD-Agent 实测机理标签 → 桶（生产库 12 条逐一校准）
    CASES = [
        # (name, description片段, 期望桶)
        ("volratio20", "Twenty-day abnormal volume ratio", "量与流动性"),
        ("volcv20", "Liquidity Stability Factor volume coefficient", "量与流动性"),
        ("volume_autocorr_10d", "Volume Autocorrelation persistence", "量与流动性"),
        ("VolumeTrend_20_60", "Slow Volume-Regime Trend volume", "量与流动性"),
        ("VOL_CONCENTRATION_10D", "Volume Concentration spike", "量与流动性"),
        ("AMIHUD_ILLIQ_20D", "Amihud illiquidity price impact", "量与流动性"),
        ("range1", "Intraday Range high-low spread", "波动率"),
        ("RANGE_EXPANSION_20D", "Volatility News-Burst range expansion", "波动率"),
        ("F_range_expansion", "Volatility Regime range expansion", "波动率"),
        ("LOW_VOLATILITY_20D", "low volatility stocks", "波动率"),
        ("CORR5", "Price-Volume Co-movement correlation", "量价交互"),
        ("pv_corr_10d", "Price-Volume Confirmation rolling correlation", "量价交互"),
        ("VOL_RET_CORR_20D", "Price-Volume Interaction volume return correlation", "量价交互"),
        ("REV_5D_VOLADJ", "volume adjusted reversal 5 day", "动量反转"),
        ("mom20", "momentum 20 day", "动量反转"),
        ("KLOW", "Candlestick Shape lower shadow support", "外部-其他"),  # 形态族无对应桶 → 兜底
        ("totally_unknown_xyz", "some exotic construction", "外部-其他"),
    ]

    def test_anchored_buckets(self):
        from finance_agent.factors.harvest import classify_category
        for name, desc, expected in self.CASES:
            assert classify_category(name, desc) == expected, f"{name} → 期望 {expected}"

    def test_never_raises_and_never_empty(self):
        from finance_agent.factors.harvest import classify_category
        assert classify_category("", "") == "外部-其他"
        assert classify_category(None, None) == "外部-其他"  # 防御：非字符串输入
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/factors/test_harvest.py::TestClassifyCategory -v`
Expected: FAIL（classify_category 未定义）

- [ ] **Step 3: Write minimal implementation**

`harvest.py`，`DEFAULT_CATEGORY = "外部"` 行替换为桶表 + 分类函数（`DEFAULT_CATEGORY` 保留但仅作既有测试兼容引用点——实施时 grep 全仓确认引用处全部改走 `classify_category` 后可删）：

```python
# 机理分桶（update-harvest-factor-category）：有序关键词表，首中即停；
# 顺序刻意：量价交互（含 "price-volume"）先于量与流动性（含 "volume"）——
# 否则 "price-volume" 会被 "volume" 先吃掉。兜底桶保底不丢因子。
CATEGORY_BUCKETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("量价交互", ("price-volume", "price volume", "co-movement", "pv corr", "volume return correlation")),
    ("量与流动性", ("volume", "liquidity", "vol ratio", "amihud", "illiq", "换手", "turnover")),
    ("波动率", ("volatility", "range", "波动")),
    ("动量反转", ("momentum", "reversal", "动量", "反转")),
)
CATEGORY_FALLBACK = "外部-其他"


def classify_category(name: Any, description: Any) -> str:
    """机理关键词分桶（确定性，零 LLM）；未命中落兜底桶；异常输入安全降级兜底。"""
    text = " ".join(str(part) for part in (name, description) if part).lower()
    for bucket, keywords in CATEGORY_BUCKETS:
        if any(keyword in text for keyword in keywords):
            return bucket
    return CATEGORY_FALLBACK
```

注意：`test_anchored_buckets` 里 `REV_5D_VOLADJ` 的 description 含 "reversal" → 动量反转 ✓；`LOW_VOLATILITY_20D` 含 "volatility" → 波动率 ✓；`AMIHUD_ILLIQ_20D` 含 "illiq" → 量与流动性 ✓。若锚定集有未过用例，先查匹配顺序再调关键词表（不许为过测试在函数里写 name 特判——映射表本身可调）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/factors/test_harvest.py::TestClassifyCategory -v`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/factors/harvest.py tests/factors/test_harvest.py
git commit -m "feat(factors): 机理分桶关键词表 + classify_category（harvest-category 1/3）"
```

---

### Task 2: 收割三处 payload 构造点改走分类

**Files:**
- Modify: `src/finance_agent/factors/harvest.py:405-493`（三处 `"category": DEFAULT_CATEGORY`）
- Test: `tests/factors/test_harvest.py`

**Interfaces:**
- Consumes: Task 1 的 `classify_category`
- Produces: 收割 payload 全部候选（含转译失败/不可翻译/过闸）category = 分类结果

- [ ] **Step 1: Write the failing test**

复用 `tests/factors/test_harvest.py` 现有 stub client fixture（该文件已有「收割批三态混合」用例）：

```python
def test_harvest_payload_categories_classified(self, tmp_path, _harvest_fixture):
    # _harvest_fixture = 现有三态混合用例的构造；候选 description 分别带
    # volume/volatility/exotic 标签
    db, summary, payload_categories = _harvest_fixture
    assert "量与流动性" in payload_categories
    assert "波动率" in payload_categories
    assert "外部-其他" in payload_categories
    assert "外部" not in payload_categories  # 硬编码单桶不再出现
```

（payload categories 的抓取方式：stub client 捕获 prompt 或直接对 `run_factor_harvest` 后查 `factors` 表 category 列——以文件内现有断言模式为准。）

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/factors/test_harvest.py -k category -v`
Expected: FAIL（category 仍全为「外部」）

- [ ] **Step 3: Write minimal implementation**

三处 payload dict 的 `"category": DEFAULT_CATEGORY` 各替换为：

```python
                    "category": classify_category(name, description),
```

（转译失败与不可翻译路径上 `description` 变量在作用域内已存在——见现实现循环体开头 `description = str(candidate.get("description") or "").strip()`。）

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/factors/test_harvest.py -v`
Expected: 全 PASS（含既有收割场景回归——若现用例断言 category=="外部"，改为断言 ∈ 桶集）

- [ ] **Step 5: Commit**

```bash
git add src/finance_agent/factors/harvest.py tests/factors/test_harvest.py
git commit -m "feat(factors): 收割 payload 走机理分桶（harvest-category 2/3）"
```

---

### Task 3: 存量迁移脚本

**Files:**
- Create: `scripts/migrate_factor_category.py`
- Test: `tests/factors/test_category_migration.py`

**Interfaces:**
- Consumes: Task 1 的 `classify_category`；`library._connect`（事件写入走 `record_status_change` 同款 SQL——迁移是 category 改写非状态迁移，落 `factor_events` 用既有 `event="status_change"` 语义不合适，直接 INSERT `event="category_migration"` 行，from/to 记 detail）
- Produces: `migrate_category(db_path) -> {"migrated": int, "skipped": int}`（仅 `category="外部"` 行；幂等）

（scripts 模块在测试中可直接 import：`tests/` 是包、pytest 自动把仓库根插进 `sys.path`，先例 `tests/test_deploy_script.py` 的 `from scripts.deploy import ...`。）

- [ ] **Step 1: Write the failing test**

```python
"""scripts/migrate_factor_category.py 单测：幂等迁移 + 事件留痕。"""
from __future__ import annotations

import sqlite3

from finance_agent.factors.library import add_factor, init_library
from scripts.migrate_factor_category import migrate_category


def test_migrate_and_idempotent(tmp_path):
    db = str(tmp_path / "factor_library.db")
    fid_vol = add_factor(expression="ts_mean(volume,5)", hypothesis="[Volume Factor] x",
                         category="外部", status="active", db_path=db)
    fid_range = add_factor(expression="div(sub(high,low),close)", hypothesis="[Intraday Range] y",
                           category="外部", status="active", db_path=db)
    result = migrate_category(db_path=db)
    assert result == {"migrated": 2, "skipped": 0}
    conn = sqlite3.connect(db)
    rows = {r[0]: r[1] for r in conn.execute("SELECT id, category FROM factors")}
    events = conn.execute(
        "SELECT COUNT(*) FROM factor_events WHERE event='category_migration'"
    ).fetchone()[0]
    conn.close()
    assert rows[fid_vol] == "量与流动性"
    assert rows[fid_range] == "波动率"
    assert events == 2
    # 幂等：重跑零迁移（category 已非「外部」）
    assert migrate_category(db_path=db) == {"migrated": 0, "skipped": 0}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/factors/test_category_migration.py -v`
Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: Write minimal implementation**

```python
"""存量因子 category 机理分桶迁移（update-harvest-factor-category Task 3）。

对 ``category="外部"`` 行按 ``classify_category``（同一映射函数，单一真相源）
重写 + ``factor_events`` 落 ``category_migration`` 事件（detail 记 from/to）。
幂等可重跑（仅迁移仍为「外部」的行）。历史 BUCKET_QUOTA 事件不改写。
用法: uv run python scripts/migrate_factor_category.py [--db PATH]
"""
from __future__ import annotations

import argparse

from finance_agent.factors.harvest import classify_category
from finance_agent.factors.library import _connect, _now_iso, list_factors, init_library

_OLD_CATEGORY = "外部"


def migrate_category(*, db_path: str | None = None) -> dict[str, int]:
    init_library(db_path)
    migrated = 0
    conn = _connect(db_path)
    try:
        for row in list_factors(db_path=db_path):
            if str(row.get("category")) != _OLD_CATEGORY:
                continue
            fid = int(row["id"])
            new_category = classify_category(str(row.get("expression")), str(row.get("hypothesis")))
            conn.execute("UPDATE factors SET category = ? WHERE id = ?", (new_category, fid))
            conn.execute(
                "INSERT INTO factor_events (factor_id, event, from_status, to_status, detail, created_at) "
                "VALUES (?, 'category_migration', NULL, NULL, ?, ?)",
                (
                    fid,
                    f'{{"from_category": "{_OLD_CATEGORY}", "to_category": "{new_category}"}}',
                    _now_iso(),
                ),
            )
            migrated += 1
        conn.commit()
    finally:
        conn.close()
    return {"migrated": migrated, "skipped": 0}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=None)
    args = parser.parse_args()
    print(migrate_category(db_path=args.db))


if __name__ == "__main__":
    main()
```

注意两点实施核对：① 分类输入用 `expression + hypothesis`（存量行无原始 description，hypothesis 首段已含机理标签 `[RD-Agent/名] 标签…`）；② `_connect` / `_now_iso` 是 library 私有——脚本属部署类工具（`scripts/`），私有导入加 `# noqa: PLC2701` 注释（仓库先例同款）。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/factors/test_category_migration.py tests/factors/test_harvest.py -v`
Expected: 全 PASS

- [ ] **Step 5: 全量回归 + lint + 类型**

Run: `uv run pytest && uv run ruff check && uv run mypy`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add scripts/migrate_factor_category.py tests/factors/test_category_migration.py
git commit -m "feat(factors): 存量 category 机理分桶幂等迁移（harvest-category 3/3）"
```

---

### Task 4: 生产迁移 + 实跑验证（运维任务）

**Files:**
- 无代码变更

- [ ] **Step 1: 生产库备份**

```bash
cp data/factor_library.db data/factor_library.db.bak-category-$(date +%Y%m%d-%H%M%S)
```

- [ ] **Step 2: 跑迁移并核对**

```bash
uv run python scripts/migrate_factor_category.py
```

Expected: `migrated=12`（12 条 external 存量按机理落桶：量族 ~5、波动 ~3、量价交互 ~3、KLOW → 外部-其他），再跑一次 `migrated=0`。

- [ ] **Step 3: 次日收割实跑验证**

次日 16:45 收割批后查 `factor_library.db`：新入库/新拒收候选 category ∈ 桶集；`GET /api/v1/ops/jobs` 收割摘要正常。红线：16:00–16:45 收割窗口与 17:55–20:45 日报窗口不重启后端。

- [ ] **Step 4: 报告落档 + tasks 回填**

`tests/validation/` 报告 + delta tasks.md 全勾。

- [ ] **Step 5: Commit**

```bash
git add openspec/changes/update-harvest-factor-category/tasks.md tests/validation/
git commit -m "docs(factors): harvest-category 验证报告与 tasks 回填"
```
