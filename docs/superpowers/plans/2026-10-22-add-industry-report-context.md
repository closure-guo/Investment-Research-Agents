# Industry Report Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development（或单 agent 按序执行）

**Goal:** 把 228 条行业研报按「标的所属行业」确定性接进基本面分析师上下文，未覆盖口径显式说明。

**Architecture:** 只读聚合（SQL 过滤 + 确定性排序）→ `state["industry_report_digest"]` → 基本面上下文段 → citation 回声源。零 LLM、零新依赖、零新表。

**Tech Stack:** Python 3.14 / pytest / SQLite（`data/research_reports.db`）

## Global Constraints

- 归一只做 `NFKC` + 去空白 + 剥离末尾 `Ⅱ/Ⅲ/II/III`；禁止编辑距离/子串/分词匹配
- 别名表只收录实测确认项，值必须存在于 `tests/fixtures/industry_report_vocab.tsv`
- 时点硬约束 `publish_date <= as_of`；默认窗口 90 天、最多 8 篇，可用 `INDUSTRY_DIGEST_MAX_AGE_DAYS` / `INDUSTRY_DIGEST_MAX_REPORTS` 覆盖
- 三态：`ok` / `stale` / `unmatched`；只有「库内无 industry 数据」才返回 `None`
- 读库异常 fail-open 返回 `None`，不阻断分析
- 段文本硬上限：8 条 × 标题 ≤ 80 字
- 不新增依赖；不改 `analyst-context-budget` 的裁剪顺序

---

### Task 1: 归一与别名表

**Files:**
- Modify: `src/finance_agent/data/research_reports.py`
- Test: `tests/data/test_industry_digest.py`

**Interfaces:**
- Produces: `normalize_industry(name: str | None) -> str`；`INDUSTRY_ALIASES: dict[str, str]`

- [ ] **Step 1: 写失败测试**

```python
def test_normalize_strips_level_suffix():
    assert rr.normalize_industry("白酒Ⅱ") == "白酒"
    assert rr.normalize_industry("银行 II") == "银行"
    assert rr.normalize_industry("ＩＴ服务") == "IT服务"   # NFKC 全角
    assert rr.normalize_industry("  电 池 ") == "电池"
    assert rr.normalize_industry(None) == ""

def test_alias_values_exist_in_vocab_snapshot():
    vocab = {line.split("\t")[0] for line in Path("tests/fixtures/industry_report_vocab.tsv").read_text("utf-8").splitlines()}
    for key, value in rr.INDUSTRY_ALIASES.items():
        assert value in vocab, f"别名 {key}->{value} 不在实测词表内"
```

- [ ] **Step 2: 跑测试确认失败**（`AttributeError: normalize_industry`）
- [ ] **Step 3: 最小实现**（`unicodedata.normalize` + `split()` 去空白 + `while` 去后缀）
- [ ] **Step 4: 跑测试确认通过**

---

### Task 2: 只读查询 + 聚合

**Files:**
- Modify: `src/finance_agent/data/research_reports_store.py`（`list_industry_reports`）
- Modify: `src/finance_agent/data/research_reports.py`（`get_industry_digest`）
- Test: `tests/data/test_industry_digest.py`

**Interfaces:**
- Produces: `get_industry_digest(industry, *, as_of=None, db_path=None, max_reports=None, max_age_days=None) -> dict | None`

- [ ] **Step 1: 失败测试**（ok 聚合 / 未来剔除 / stale / unmatched / 空库 None / 别名命中 / 异常 fail-open）
- [ ] **Step 2: 确认红**
- [ ] **Step 3: 实现**：候选集 = `{norm(name), norm(alias.get(norm(name)))}`；SQL `industry IN (...)`（按归一后比对需读全量后在 Python 过滤，因库内存的是原始名 → 实现为：先用 `report_type='industry'` + 日期过滤取行，再按归一后名字在应用层过滤，条数受 `limit` 约束）
- [ ] **Step 4: 确认绿**

> 实现备注：SQL 里存的是原始名（`白酒Ⅱ`），归一在应用层做；为避免全表扫描，先用 `publish_date` 区间 + `report_type` 缩小，再按归一名字筛选。228 行规模可接受。

---

### Task 3: 渲染

**Files:**
- Modify: `src/finance_agent/data/research_reports.py`（`format_industry_digest`）
- Test: `tests/data/test_industry_digest.py`

- [ ] **Step 1: 失败测试**（含「仅元数据」、防锚定、stale 说明、unmatched 不说「无研报」、标题截断）
- [ ] **Step 2: 确认红 → Step 3 实现 → Step 4 确认绿**

---

### Task 4: 注入 state 与上下文

**Files:**
- Modify: `src/finance_agent/state.py`、`src/finance_agent/nodes/fetch.py`、`src/finance_agent/nodes/analysts.py`
- Test: `tests/nodes/test_industry_report_context.py`

- [ ] **Step 1: 失败测试**（fetch 注入 / 基本面注入 / 无数据不占位 / 宏观·技术·舆情不消费）
- [ ] **Step 2: 确认红 → Step 3 实现 → Step 4 确认绿**

---

### Task 5: citation 回声源

**Files:**
- Modify: `src/finance_agent/citation.py`
- Test: `tests/test_citation_industry_digest.py`（对齐 `tests/test_citation_strategy_digest.py` 写法）

- [ ] **Step 1: 失败测试 → Step 2 红 → Step 3 实现 → Step 4 绿**

---

### Task 6: prompt + 回归

- [ ] `prompts/fundamental_analyst.md` 数据清单增行
- [ ] `uv run python scripts/deploy_prompts.py`
- [ ] `uv run pytest tests/data/test_industry_digest.py tests/nodes/test_industry_report_context.py tests/test_citation_industry_digest.py -q`
- [ ] `uv run pytest -q`（全量，记录 passed/failed）
- [ ] `uv run ruff check` / `uv run mypy`
- [ ] `openspec validate add-industry-report-context --strict`
