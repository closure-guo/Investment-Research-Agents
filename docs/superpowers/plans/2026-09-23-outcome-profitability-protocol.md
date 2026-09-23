# add-outcome-profitability-protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为「证明 agent 赚钱能力」的 outcome 收益评估立口径（metrics.md §1.8）、预登记门禁、两句式结论与健康检查工具链——零生产代码变更，全部落在 evals 侧。

**Architecture:** 纯增量：文档登记（metrics.md §1.8 + §2 切点）+ 预登记文档（复用 `evals/causal_ablation/preregister.py` 机制，扩展 outcome 门禁字段）+ 新包 `evals/outcome/`（口径常量 / 结论句式 / 报告 status 断言 / 健康检查 CLI）。复用而非另造：report_status、status_index、preregister 三套既有机制全部复用。

**Tech Stack:** 纯 Python（sqlite3 只读查询 / dataclass / argparse），pytest。无 LLM 调用、无生产代码改动。

## Global Constraints

- **工作区**：`.worktrees/outcome-eval`（分支 `feat/outcome-profitability-eval`）；所有命令在此目录执行。四个 delta 提案目录（`openspec/changes/{add-outcome-profitability-protocol,update-decision-settlement-contract,add-forward-paper-trading-cohort,add-backtest-leakage-controls}/`）在本分支待提交。
- **口径红线**：口径变更先改 `docs/evals/metrics.md` §1 再动代码（AGENTS.md）。本 delta 任何代码不得复述口径文字，只引用 `evals/outcome/caliber.py` 常量 + metrics.md §1.8。
- **文档红线**：`docs/evals/` 下的非报告文档（含 metrics.md）**不得复现报告 status 头字面标记**（会被 status_index 扫描器误判为报告，README 已警告）——§1.8 中提及该字段名时不带粗体星号。
- **测试隔离**：测试一律用 `tmp_path` 注入 DB 路径（incident 031 教训：绝不落真实 `data/sessions.db`）。
- **门禁字段（outcome）**：主指标 / MDE / 决策阈值 / 样本量依据 / 停止规则 / 成本分型 / 泄漏控制（`OUTCOME_REQUIRED_FIELDS`）。
- **MDE 换算（预登记文档口径）**：胜率（单样本 vs 50%，α=0.05/power=0.8）MDE = 1.4008/√n；均值超额（σ 假设 10pp）MDE = 0.2802/√n。n=30 → 25.6pp / 5.1pp；n=100 → 14.0pp / 2.8pp。
- **既有约束**：`reports/` 已 gitignore；`evals` 为根级包（pytest `pythonpath=["src"]` + rootdir 机制，测试直接 `from evals... import`）。
- **命令**：`uv run pytest tests/evals/outcome -v`；`uv run ruff check evals tests`；`uv run mypy evals/outcome`（触碰文件零新增错误）。
- **commit 风格**：中文描述 + conventional 前缀（`docs(evals):` / `feat(evals):` / `test(evals):`）。

---

### Task 0: 提交四个 delta 提案入分支

**Files:**
- Add: `openspec/changes/{add-outcome-profitability-protocol,update-decision-settlement-contract,add-forward-paper-trading-cohort,add-backtest-leakage-controls}/`（已存在于工作区，未跟踪）

- [ ] **Step 1: 确认四个提案目录完整且 validate 全绿**

```bash
cd .worktrees/outcome-eval
for c in add-outcome-profitability-protocol update-decision-settlement-contract add-forward-paper-trading-cohort add-backtest-leakage-controls; do openspec validate "$c" --strict; done
```
Expected: 4 个 "is valid"

- [ ] **Step 2: 提交**

```bash
git add openspec/changes/add-outcome-profitability-protocol openspec/changes/update-decision-settlement-contract openspec/changes/add-forward-paper-trading-cohort openspec/changes/add-backtest-leakage-controls
git commit -m "docs(openspec): 立四个 outcome 收益评估 delta——口径协议 / 结算契约 / forward cohort / 回测泄漏控制"
```

---

### Task 1: metrics.md §1.8 口径登记 + §2 切点行 + 文档护栏测试

**Files:**
- Modify: `docs/evals/metrics.md`（§1.7 末尾新增 §1.8；§2「决策契约口径切点（2026-09-21…）」段后插入新切点段）
- Create: `tests/evals/outcome/__init__.py`
- Test: `tests/evals/outcome/test_caliber_doc.py`

**Interfaces:**
- Produces: `docs/evals/metrics.md` §1.8「Outcome 收益指标」小节（后续任务与 delta 2–4 引用锚点）

- [ ] **Step 1: 写失败测试**

```python
"""§1.8 口径登记护栏：口径条目是评估协议的唯一权威引用锚，防误删/漂移（spec evaluation 口径与预登记）。"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]  # tests/evals/outcome/ → repo root


def _metrics_text() -> str:
    return (_ROOT / "docs/evals/metrics.md").read_text(encoding="utf-8")


def test_section_1_8_registered_with_core_caliber():
    text = _metrics_text()
    assert "### 1.8 Outcome 收益指标" in text
    for anchor in ("T+20", "000300.SH", "回避正确率", "settled", "1.4008", "0.2802", "runs.jsonl"):
        assert anchor in text, f"§1.8 缺口径锚点: {anchor}"


def test_timeline_has_outcome_switchpoint():
    text = _metrics_text()
    assert "Outcome 收益口径切点（2026-09-23" in text
    assert "add-outcome-profitability-protocol" in text


def test_no_report_status_literal_marker():
    """docs/evals 非报告文档不得复现 status 头字面标记（README 扫描器警告）。"""
    assert "**status**:" not in _metrics_text()
```

- [ ] **Step 2: 跑测确认红**

Run: `uv run pytest tests/evals/outcome/test_caliber_doc.py -v`（需先 `touch tests/evals/outcome/__init__.py`）
Expected: FAIL（缺 §1.8）

- [ ] **Step 3: 写 metrics.md §1.8**

在 `docs/evals/metrics.md` §1.7 末尾（`## 2. 时间线` 前的 `---` 之前）插入：

````markdown
### 1.8 Outcome 收益指标（delta `add-outcome-profitability-protocol`，2026-09-23 登记；启用切点见 §2）

「赚钱能力」（outcome 收益）的评估口径。口径变更 SHALL 先改本节再动代码；本节不复制读数，读数在收口报告与 `runs.jsonl`。

| 项 | 口径 |
|---|---|
| ① 主指标 | 逐决策 **T+20 交易日相对沪深300（000300.SH）超额收益**：均值（`excess_return`）与胜率（resolved_win/(win+loss)，±2% 中性带，沿用 track-record 判定口径，不另造定义） |
| ② 人口划分 | 主结论仅基于可执行决策（long/short）；neutral（watch/hold）以**回避正确率** = avoidance_win/(avoidance_win+avoidance_loss) 作辅助指标，SHALL NOT 进主结论（实现见 delta `update-decision-settlement-contract`） |
| ③ 辅助观测 | T+5 / T+10 窗口由 daily_marks 派生，只观测不判定；不参与胜率与结论句；派生例程与口径同 §1.5 的均值差纪律（点估计给口径、非点估计不冒充） |
| ④ 红线 | settled 可执行样本 <10：SHALL NOT 报任何胜率 / 平均超额 / 赚钱能力措辞，只报样本数与「样本积累中」（与 track-record 基础统计展示门槛一致，泛化为评估纪律） |
| ⑤ 收口纪律 | 健康检查（结算成功率 ≥0.90 / 行情缺失率 ≤0.10 / integrity 零不一致 / 记账完整率）→ 异常行人工终裁（对照表落 `tests/validation/`）→ 两句式结论（显著方向带 CI ｜ 分辨率不足带 MDE；裸「未获统计支持」非法）→ 收口报告带生命周期字段（字面格式见 §1.7④；本文档不复现该字面标记）→ 本表 §2 时间线追加 + `runs.jsonl` 追加（`type: outcome-forward` / `type: outcome-backtest`） |
| ⑥ 泄漏控制 | forward 腿为金标准（零泄漏假设）；回测腿须过干净窗口判定 + 泄漏探针披露（默认阈值 0.60，实现见 delta `add-backtest-leakage-controls`）；深历史批次永久定位通路验证；两腿分歧先归因（口径见 `evaluation`「Forward 与回测双腿互证」） |
| ⑦ MDE 换算 | 胜率（单样本 vs 50%，α=0.05 / power=0.8）：MDE = 1.4008/√n；均值超额（σ 假设 10pp）：MDE = 0.2802/√n。n=30 → 胜率 25.6pp / 均值 5.1pp；n=100 → 14.0pp / 2.8pp。推断单元按标的聚类，ICC 折算有效 n 随读数披露（同 §1.7 推断单元纪律） |

首个预登记：`evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md`（门禁字段：主指标 / MDE / 决策阈值 / 样本量依据 / 停止规则 / 成本分型 / 泄漏控制；解析见 `evals/causal_ablation/preregister.py::OUTCOME_REQUIRED_FIELDS`，与因果消融的差异 = 无 rubric、增成本分型与泄漏控制）。
````

在 §2 的「决策契约口径切点（2026-09-21，delta `require-watch-hold-rationale`，未跑批）」段之后插入：

```markdown
**Outcome 收益口径切点（2026-09-23，delta `add-outcome-profitability-protocol`，未跑批）**：outcome 收益指标口径登记 §1.8（主指标 T+20 超额 / 回避辅助 / settled<10 红线 / 收口纪律 / 泄漏控制），首个 outcome 预登记落 `evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md`。**本切点只登记口径与纪律，不改任何结算行为**——T+20 结算窗口、回避判定、标准化入场价的落地在 delta `update-decision-settlement-contract`（其切点另行登记）；读数腿（forward cohort / walk-forward 回测）开跑前置 = 两 delta 均已落地，且首个读数之前 §1.8 与预登记不得再改（改须走 §1 修订 + 新预登记版本 + 新切点行）。
```

- [ ] **Step 4: 跑测确认绿**

Run: `uv run pytest tests/evals/outcome/test_caliber_doc.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add docs/evals/metrics.md tests/evals/outcome/__init__.py tests/evals/outcome/test_caliber_doc.py
git commit -m "docs(evals): metrics.md §1.8 outcome 收益口径登记 + §2 切点行（含文档护栏测试）"
```

---

### Task 2: preregister.py 扩展 outcome 门禁字段 + 首个 outcome 预登记文档

**Files:**
- Modify: `evals/causal_ablation/preregister.py`
- Create: `evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md`
- Test: `tests/evals/causal_ablation/test_preregister.py`（追加两个测试类）

**Interfaces:**
- Produces: `OUTCOME_REQUIRED_FIELDS: tuple[str, ...]`；`parse_preregister(text, *, required_fields=REQUIRED_FIELDS) -> Preregistration`；`find_latest_preregister(dir_path, *, name_contains=None, required_fields=REQUIRED_FIELDS)`；`assert_preregistered(dir_path, *, name_contains=None, required_fields=REQUIRED_FIELDS)`——默认值向后兼容（现有调用零改动）
- Consumes: 既有 `Preregistration.fields / .issues / .valid`

- [ ] **Step 1: 写失败测试**

在 `tests/evals/causal_ablation/test_preregister.py` 追加（import 行同步扩 `OUTCOME_REQUIRED_FIELDS`）：

```python
class TestOutcomeGate:
    """outcome 收益评估门禁字段（metrics.md §1.8）：无 rubric、增成本分型与泄漏控制。"""

    OUTCOME_GOOD = """# Outcome 预登记
- 主指标: T+20 相对 000300 超额收益
- MDE: 均值 5.1pp@n=30
- 决策阈值: CI 判定，下限>0 显著为正；依据：单样本 CI 换算见 §4
- 样本量依据: n>=30 可执行 settled
- 停止规则: 健康检查不过作废
- 成本分型: forward 166k tokens/标的
- 泄漏控制: 探针阈值 60%
"""

    def test_outcome_fields_valid(self):
        p = parse_preregister(self.OUTCOME_GOOD, required_fields=OUTCOME_REQUIRED_FIELDS)
        assert p.valid is True, p.issues

    def test_outcome_missing_leakage_field_reported(self):
        text = self.OUTCOME_GOOD.replace("- 泄漏控制: 探针阈值 60%\n", "")
        p = parse_preregister(text, required_fields=OUTCOME_REQUIRED_FIELDS)
        assert any("泄漏控制" in i for i in p.issues)

    def test_default_required_fields_unchanged(self):
        """因果消融默认门禁不变：outcome 文档按默认口径应报缺 rubric 版本。"""
        p = parse_preregister(self.OUTCOME_GOOD)
        assert any("rubric 版本" in i for i in p.issues)

    def test_real_outcome_preregister_document_valid(self):
        root = Path(__file__).resolve().parents[3]
        doc = root / "evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md"
        p = parse_preregister(
            doc.read_text(encoding="utf-8"), required_fields=OUTCOME_REQUIRED_FIELDS
        )
        assert p.valid is True, p.issues


class TestOutcomeLookup:
    def test_name_contains_isolates_outcome_docs(self, tmp_path: Path):
        (tmp_path / "2026-09-17-p2-family-b.md").write_text("# P2\n- 主指标: B1\n", encoding="utf-8")
        (tmp_path / "2026-09-23-outcome-forward-and-backtest.md").write_text(
            TestOutcomeGate.OUTCOME_GOOD, encoding="utf-8"
        )
        found = find_latest_preregister(
            tmp_path, name_contains="outcome", required_fields=OUTCOME_REQUIRED_FIELDS
        )
        assert found is not None
        assert found.path.name == "2026-09-23-outcome-forward-and-backtest.md"
        assert found.valid is True
```

- [ ] **Step 2: 跑测确认红**

Run: `uv run pytest tests/evals/causal_ablation/test_preregister.py -v`
Expected: FAIL（`ImportError: cannot import name 'OUTCOME_REQUIRED_FIELDS'`）

- [ ] **Step 3: 实现 preregister.py 扩展**

`REQUIRED_FIELDS` 定义之后新增常量：

```python
# outcome 收益评估门禁字段（delta add-outcome-profitability-protocol，口径 metrics.md §1.8）：
# 与因果消融的差异——无 rubric 版本（判定零 LLM），增成本分型与泄漏控制（两腿读数特有）。
OUTCOME_REQUIRED_FIELDS: tuple[str, ...] = (
    "主指标",
    "MDE",
    "决策阈值",
    "样本量依据",
    "停止规则",
    "成本分型",
    "泄漏控制",
)
```

`Preregistration` 数据类加字段（默认值保持因果消融口径，向后兼容）：

```python
@dataclass
class Preregistration:
    path: Path
    fields: dict[str, str]
    raw: str
    required_fields: tuple[str, ...] = REQUIRED_FIELDS
```

`issues` 属性中 `for field in REQUIRED_FIELDS:` 改为 `for field in self.required_fields:`。

三个函数签名与实现同步（默认值 `REQUIRED_FIELDS` 不变）：

```python
def parse_preregister(
    text: str, *, required_fields: tuple[str, ...] = REQUIRED_FIELDS
) -> Preregistration:
    fields: dict[str, str] = {}
    for line in (text or "").splitlines():
        m = _FIELD_RE.match(line.strip())
        if m:
            fields[m.group("key").strip()] = m.group("value").strip()
    return Preregistration(
        path=Path("<inline>"), fields=fields, raw=text or "", required_fields=required_fields
    )


def find_latest_preregister(
    dir_path: Path,
    *,
    name_contains: str | None = None,
    required_fields: tuple[str, ...] = REQUIRED_FIELDS,
) -> Preregistration | None:
    ...
    parsed = parse_preregister(latest.read_text(encoding="utf-8"), required_fields=required_fields)
    ...


def assert_preregistered(
    dir_path: Path,
    *,
    name_contains: str | None = None,
    required_fields: tuple[str, ...] = REQUIRED_FIELDS,
) -> Preregistration:
    found = find_latest_preregister(dir_path, name_contains=name_contains, required_fields=required_fields)
    ...
```

- [ ] **Step 4: 写首个 outcome 预登记文档**

创建 `evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md`：

````markdown
# Outcome 收益评估预登记（forward + 回测双腿）

> delta `add-outcome-profitability-protocol` 的预登记文档；口径唯一定义在 `docs/evals/metrics.md` §1.8。
> 本文件下方各节的列表使用「表格 + 散文」，避免出现 `- 字段: 值` 形态的伪门禁行（解析器会全文档扫描）。

## 0. 门禁字段（机器可读，`preregister.py` 解析；口径细节见下方各节）

- 主指标: 逐决策 T+20 交易日相对沪深300（000300.SH）超额收益的均值与胜率（±2% 中性带口径）
- MDE: 均值超额（σ 假设 10pp）n=30 → 5.1pp、n=100 → 2.8pp；胜率 n=30 → 25.6pp、n=100 → 14.0pp；换算依据见 §4
- 决策阈值: CI 判定——均值超额 95% CI 下限 > 0 显著为正（赚钱能力主张成立）；上限 < 0 显著为负（先分桶归因再处置）；跨越 0 判分辨率不足；依据：单样本 CI 与 §4 MDE 对应关系
- 样本量依据: forward 腿红线 ≥10 可执行 settled、完整结论 ≥30、扩样 ≥100（metrics.md §1.8⑦ 换算）；回测腿按 MDE 反算并以预算上限封顶；推断单元簇 = 标的（ICC 折算有效 n 随读数披露）
- 停止规则: ① 健康检查不过（结算成功率 <0.90 / 行情缺失率 >0.10 / integrity 不一致非零）→ 本批读数作废、先修数据；② 两腿分歧超阈未归因 → 结论挂起（标记「待归因」）；③ 回测腿探针命中率 >0.60 → 降级「泄漏污染下的上界证据」句式；④ 单批成本超申报值 20% → 停机复核
- 成本分型: forward 腿 = 每标的 1 次 deep 全流程（pilot 实测 ≈166k tokens，由 cohort 跑批承载）；回测腿 = 回放 ≈166k tokens × 样本量 × 3 重复 + 探针（标的数 × 3–5 问）；判定与统计零 LLM 调用
- 泄漏控制: forward 腿为金标准（真实时点、零泄漏假设）；回测腿须过干净窗口判定（决策日距跑批日 ≥20 交易日）+ 泄漏探针披露（阈值 0.60）；深历史（2025 年前样本）永久定位通路验证，不产 skill 结论

状态：**已转正（2026-09-23）**——门禁字段齐备。**前置未满足前不得开跑读数腿**：T+20 结算窗口、回避判定、标准化入场价的实现（delta `update-decision-settlement-contract`）未落地前，forward 腿读数口径不可用；本预登记的实施进度不改变这一前置关系。

## 1. 主指标与人口划分

| 项 | 定义 |
|---|---|
| 主指标 | 逐决策 T+20 相对 000300.SH 超额收益：均值 + 胜率（resolved_win/(win+loss)，±2% 中性带，与 track-record 判定口径同源） |
| 主人口 | 可执行决策（long/short）——主结论唯一人口 |
| 辅助人口 | neutral（watch/hold）回避正确率 = avoidance_win/(avoidance_win+avoidance_loss)，独立字段、不进主结论 |
| 辅助观测 | T+5/T+10 由 daily_marks 派生，只观测不判定 |
| 红线 | settled 可执行样本 <10 不报任何胜率类读数 |

## 2. 双腿设计

forward 腿（金标准）：cohort 定时跑批的真实管线观点，逐条落 predictions 走生产结算；样本随日历积累，无泄漏假设，局限 = 慢。

回测腿（快，带泄漏风险）：as-of 快照 + 干净窗口（近端已完成 T+20 结算的决策日）+ 泄漏探针；读数与 forward 并列报告、标注腿别；分歧超阈先归因（泄漏 / regime 漂移 / 执行差异）。

两腿同口径清单：结算窗口 T+20、基准 000300.SH、结算入场价 = 决策归属日收盘（收盘后决策/非交易日 → 次一交易日收盘，两腿同源派生；参考价仅展示与盯市）、胜率定义、中性带 ±2%。

## 3. 收口流程

① 健康检查（`evals/outcome/health.py`，结算成功率 / 行情缺失率 / integrity / 记账完整率）；② 异常行人工终裁对照表落 `tests/validation/`；③ 两句式结论（`evals/outcome/conclusion.py`）；④ 收口报告带生命周期字段、落 `docs/evals/`；⑤ metrics.md §2 时间线 + `runs.jsonl` 追加（type: outcome-forward / outcome-backtest）。

## 4. MDE 换算

单样本口径（α=0.05 双侧、power=0.80，z 合计 2.8016）：

| 指标 | 公式 | n=30 | n=100 | n=200 |
|---|---|---|---|---|
| 胜率（vs 50%） | 2.8016 × √(0.25/n) = 1.4008/√n | 25.6pp | 14.0pp | 9.9pp |
| 均值超额（σ=10pp 假设） | 2.8016 × 0.10/√n = 0.2802/√n | 5.1pp | 2.8pp | 2.0pp |

σ 假设为外生假设（A 股个股 20 日超额收益量级）；首个读数收口时 SHALL 用实际样本 σ 复算 MDE 并披露偏差。簇 = 标的：同标的多条决策不按独立处理，有效 n 按 ICC 折算（同 metrics.md §1.7 推断单元纪律）。

## 5. 泄漏控制

| 腿 | 控制 |
|---|---|
| forward | 无泄漏（真实时点决策，无回溯数据）；局限 = 样本积累慢 |
| 回测 | 干净窗口 + 探针披露（阈值 0.60）+ 深历史永久通路验证；超阈降级「上界证据」句式 |
````

- [ ] **Step 5: 跑测确认绿**

Run: `uv run pytest tests/evals/causal_ablation/test_preregister.py tests/evals/causal_ablation/test_preregister_p1.py -v`
Expected: 全 passed（含既有用例零回归）

- [ ] **Step 6: Commit**

```bash
git add evals/causal_ablation/preregister.py evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md tests/evals/causal_ablation/test_preregister.py
git commit -m "feat(evals): outcome 门禁字段 OUTCOME_REQUIRED_FIELDS + 首个 outcome 预登记文档（delta add-outcome-profitability-protocol）"
```

---

### Task 3: evals/outcome 包——口径常量 + 两句式结论 + 收口报告 status 断言

**Files:**
- Create: `evals/outcome/__init__.py`
- Create: `evals/outcome/caliber.py`
- Create: `evals/outcome/conclusion.py`
- Create: `evals/outcome/report.py`
- Test: `tests/evals/outcome/test_conclusion.py`、`tests/evals/outcome/test_report.py`

**Interfaces:**
- Produces:
  - `caliber.PRIMARY_WINDOW_DAYS=20` / `AUX_WINDOWS=(5,10)` / `BENCHMARK_CODE="000300.SH"` / `NEUTRAL_BAND=0.02` / `MIN_SETTLED_FOR_WINRATE=10` / `FULL_CONCLUSION_SAMPLE=30` / `LEAKAGE_PROBE_THRESHOLD=0.60`
  - `conclusion.conclude_outcome(ci: tuple[float,float], *, mde: float, n: int, unit: str = "可执行决策") -> OutcomeConclusion`；`conclusion.assert_outcome_sentence_legal(sentence: str) -> None`
  - `report.assert_outcome_report(text: str, *, root: Path | None = None) -> tuple[str, str | None]`
- Consumes: `evals.causal_ablation.report_status.parse_status`

- [ ] **Step 1: 写失败测试**

`tests/evals/outcome/test_conclusion.py`：

```python
"""outcome 两句式结论纪律（spec evaluation「Outcome 读数收口纪律」第③步）。"""

import pytest
from evals.outcome.conclusion import (
    LEGAL_FORMS,
    assert_outcome_sentence_legal,
    conclude_outcome,
)


class TestConclude:
    def test_positive_when_ci_lower_above_zero(self):
        c = conclude_outcome((0.021, 0.068), mde=0.051, n=30)
        assert c.form == "positive"
        assert "显著为正" in c.sentence and "CI" in c.sentence
        assert "5.10%" in c.sentence  # MDE 格式化披露
        assert_outcome_sentence_legal(c.sentence)

    def test_negative_when_ci_upper_below_zero(self):
        c = conclude_outcome((-0.08, -0.012), mde=0.051, n=30)
        assert c.form == "negative"
        assert "显著为负" in c.sentence and "归因" in c.sentence  # 先归因后处置
        assert_outcome_sentence_legal(c.sentence)

    def test_inconclusive_when_ci_straddles_zero(self):
        c = conclude_outcome((-0.02, 0.03), mde=0.051, n=42)
        assert c.form == "inconclusive"
        assert "分辨率不足" in c.sentence and "MDE" in c.sentence
        assert_outcome_sentence_legal(c.sentence)

    def test_forms_are_legal_set(self):
        assert set(LEGAL_FORMS) == {"positive", "negative", "inconclusive"}


class TestSentenceLegality:
    def test_bare_no_support_illegal(self):
        with pytest.raises(ValueError, match="MDE"):
            assert_outcome_sentence_legal("本批未获统计支持")

    def test_significant_without_ci_illegal(self):
        with pytest.raises(ValueError, match="CI"):
            assert_outcome_sentence_legal("显著为正，赚钱能力主张成立")

    def test_inconclusive_without_mde_illegal(self):
        with pytest.raises(ValueError, match="MDE"):
            assert_outcome_sentence_legal("分辨率不足，需扩样")
```

`tests/evals/outcome/test_report.py`：

```python
"""outcome 收口报告 status 头契约（spec evaluation「Outcome 读数收口纪律」第④步）。"""

from pathlib import Path

import pytest
from evals.outcome.report import assert_outcome_report


def test_active_report_passes():
    assert assert_outcome_report("# 报告\n\n**status**: active\n") == ("active", None)


def test_missing_header_raises():
    with pytest.raises(ValueError, match="status"):
        assert_outcome_report("# 报告，无头\n")


def test_superseded_without_target_raises():
    with pytest.raises(ValueError, match="目标"):
        assert_outcome_report("**status**: superseded-by\n")


def test_superseded_pointer_must_exist(tmp_path: Path):
    text = "**status**: superseded-by: docs/evals/不存在.md\n"
    with pytest.raises(ValueError, match="不存在"):
        assert_outcome_report(text, root=tmp_path)


def test_superseded_pointer_exists_passes(tmp_path: Path):
    target = tmp_path / "docs/evals"
    target.mkdir(parents=True)
    (target / "v2.md").write_text("# v2", encoding="utf-8")
    text = "**status**: superseded-by: docs/evals/v2.md\n"
    assert assert_outcome_report(text, root=tmp_path) == (
        "superseded-by",
        "docs/evals/v2.md",
    )
```

- [ ] **Step 2: 跑测确认红**

Run: `uv run pytest tests/evals/outcome/test_conclusion.py tests/evals/outcome/test_report.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'evals.outcome'`）

- [ ] **Step 3: 实现四个模块**

`evals/outcome/__init__.py`：

```python
"""Outcome 收益评估工具链（delta add-outcome-profitability-protocol）。

口径唯一定义：docs/evals/metrics.md §1.8；预登记：evals/ablation/preregister/2026-09-23-outcome-forward-and-backtest.md。
本包零 LLM、只读为主；健康检查与结论工具供读数腿收口流程调用。
"""
```

`evals/outcome/caliber.py`：

```python
"""Outcome 口径常量（唯一权威定义 docs/evals/metrics.md §1.8；口径变更先改 §1 再动本文件）。"""

from __future__ import annotations

PRIMARY_WINDOW_DAYS = 20  # 主评估窗口（交易日）；与结算默认 horizon 一致
AUX_WINDOWS: tuple[int, ...] = (5, 10)  # 辅助观测窗（daily_marks 派生，只观测不判定）
BENCHMARK_CODE = "000300.SH"  # 沪深300
NEUTRAL_BAND = 0.02  # ±2% 中性带（胜率与回避判定共用）
MIN_SETTLED_FOR_WINRATE = 10  # settled < 10 不报胜率（红线）
FULL_CONCLUSION_SAMPLE = 30  # 完整结论门槛（n≥30）
LEAKAGE_PROBE_THRESHOLD = 0.60  # 回测腿探针降级阈值（预登记默认）
```

`evals/outcome/conclusion.py`：

```python
"""Outcome 结论句式纪律（spec evaluation「Outcome 读数收口纪律」）。

合法结论仅两句式：显著方向（带效应量与 CI）｜分辨率不足（带 MDE 与扩样方向）。
裸「未获统计支持」非法。与 causal_ablation/conclusion.py 同款纪律，判定对象不同：
causal 侧对成本阈值判定（裁剪方向），outcome 侧对 0 判定（是否存在超越基准的能力）。
"""

from __future__ import annotations

from dataclasses import dataclass

LEGAL_FORMS: tuple[str, ...] = ("positive", "negative", "inconclusive")
_BARE_NO_SUPPORT = "未获统计支持"


@dataclass(frozen=True)
class OutcomeConclusion:
    form: str
    sentence: str
    mde: float
    n: int


def conclude_outcome(
    ci: tuple[float, float],
    *,
    mde: float,
    n: int,
    unit: str = "可执行决策",
) -> OutcomeConclusion:
    """ci = 均值超额收益 95% 置信区间；跨 0 → 分辨率不足（须带 MDE）。"""
    lo, hi = ci
    if lo > 0:
        sentence = (
            f"显著为正：均值超额 {lo:+.2%} 至 {hi:+.2%}（95% CI 下限 > 0，n={n} {unit}），"
            f"赚钱能力主张成立（MDE={mde:.2%}）"
        )
        return OutcomeConclusion("positive", sentence, mde, n)
    if hi < 0:
        sentence = (
            f"显著为负：均值超额 {lo:+.2%} 至 {hi:+.2%}（95% CI 上限 < 0，n={n} {unit}），"
            f"处置前须先分桶归因（MDE={mde:.2%}）"
        )
        return OutcomeConclusion("negative", sentence, mde, n)
    sentence = (
        f"分辨率不足（MDE={mde:.2%}，CI=[{lo:+.2%}, {hi:+.2%}] 跨越 0，n={n} {unit}），"
        f"需扩样；扩样方向见预登记 §4"
    )
    return OutcomeConclusion("inconclusive", sentence, mde, n)


def assert_outcome_sentence_legal(sentence: str) -> None:
    """显著句必须写出 CI；非显著句（分辨率不足）必须写出 MDE；裸「未获统计支持」非法。"""
    if _BARE_NO_SUPPORT in sentence and "MDE" not in sentence:
        raise ValueError(f"不合格结论句（裸「未获统计支持」且缺 MDE）：{sentence!r}")
    if "显著" in sentence:
        if "CI" not in sentence:
            raise ValueError(f"不合格显著句（缺 CI）：{sentence!r}")
    elif "MDE" not in sentence:
        raise ValueError(f"不合格非显著句（分辨率不足须写出 MDE）：{sentence!r}")
```

`evals/outcome/report.py`：

```python
"""Outcome 收口报告契约：生命周期 status 头 + 取代者指针可解析。"""

from __future__ import annotations

from pathlib import Path

from evals.causal_ablation.report_status import parse_status


def assert_outcome_report(text: str, *, root: Path | None = None) -> tuple[str, str | None]:
    """校验报告 status 头；superseded-by 指针在 root 给定时须真实存在（仓库根相对路径）。"""
    status, target = parse_status(text)
    if status == "superseded-by":
        if not target:
            raise ValueError("superseded-by 必须带目标路径（取代者报告）")
        if root is not None and not (root / target).exists():
            raise ValueError(f"取代者指针不存在：{target}")
    return status, target
```

- [ ] **Step 4: 跑测确认绿**

Run: `uv run pytest tests/evals/outcome -v`
Expected: 全 passed（Task 1 的 3 例 + 本任务 9 例）

- [ ] **Step 5: Commit**

```bash
git add evals/outcome tests/evals/outcome
git commit -m "feat(evals): outcome 包——口径常量 / 两句式结论 / 收口报告 status 断言（delta add-outcome-profitability-protocol）"
```

---

### Task 4: outcome 健康检查（evals/outcome/health.py + CLI）

**Files:**
- Create: `evals/outcome/health.py`
- Test: `tests/evals/outcome/test_health.py`

**Interfaces:**
- Consumes: `finance_agent.outcome.track_record.model`（`init_track_record_tables` / `integrity_check` / `compute_snapshot_hash`）；`PREDICTIONS_STATUSES`
- Produces: `collect_outcome_health(db_path=None, *, source_type: str | None = "live", since: str | None = None) -> dict`；模块 CLI `python -m evals.outcome.health [--db PATH] [--source-type live] [--since ISO]`（stdout JSON，passed=False 时 exit 1）

- [ ] **Step 1: 写失败测试**

```python
"""outcome 健康检查（spec evaluation「Outcome 读数收口纪律」第①步）：只读、四检查 + 两告警。"""

import json
import sqlite3
from pathlib import Path

from finance_agent.outcome.track_record.model import (
    compute_snapshot_hash,
    init_track_record_tables,
)
from evals.outcome.health import collect_outcome_health, main


def _insert(db: Path, *, status: str, symbol: str = "600519", source_type: str = "live",
            entry_price: float | None = 100.0, trace: str | None = "t1",
            snapshot: dict | None = None, horizon: int = 20) -> None:
    import json as _json

    snap = snapshot if snapshot is not None else {"action": "watch", "n": symbol + status}
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO predictions (prediction_id, source_type, symbol, direction, entry_price,"
            " horizon_days, confidence, rationale_snapshot, langfuse_trace_id, status, created_at,"
            " updated_at, snapshot_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"p-{symbol}-{status}-{_json.dumps(snap)}", source_type, symbol, "long", entry_price,
             horizon, 0.6, _json.dumps(snap), trace, status, "2026-09-20T10:00:00",
             "2026-09-20T10:00:00", compute_snapshot_hash(snap)),
        )
        conn.commit()
    finally:
        conn.close()


def _db(tmp_path: Path) -> Path:
    db = tmp_path / "sessions.db"
    init_track_record_tables(db)
    return db


class TestHealth:
    def test_all_resolved_passes(self, tmp_path: Path):
        db = _db(tmp_path)
        for i in range(4):
            _insert(db, status="resolved_win", symbol=f"60{i}")
        for i in range(3):
            _insert(db, status="resolved_loss", symbol=f"61{i}")
        _insert(db, status="resolved_neutral", symbol="620")
        _insert(db, status="open", symbol="630")
        _insert(db, status="open", symbol="631")
        rep = collect_outcome_health(db, source_type="live")
        assert rep["settlement_success_rate"] == 1.0
        assert rep["market_missing_rate"] == 0.0
        assert rep["bookkeeping_completeness"] == 1.0
        assert rep["passed"] is True and rep["checks"]["integrity"] is True

    def test_unresolvable_majority_fails_market_missing(self, tmp_path: Path):
        db = _db(tmp_path)
        for i in range(5):
            _insert(db, status="resolved_win", symbol=f"70{i}")
        for i in range(5):
            _insert(db, status="unresolvable", symbol=f"71{i}")
        rep = collect_outcome_health(db, source_type="live")
        assert rep["market_missing_rate"] == 0.5
        assert rep["checks"]["market_missing"] is False
        assert rep["passed"] is False

    def test_integrity_mismatch_fails(self, tmp_path: Path):
        db = _db(tmp_path)
        _insert(db, status="resolved_win", symbol="600519")
        conn = sqlite3.connect(db)
        conn.execute("UPDATE predictions SET snapshot_hash='deadbeef'")  # 模拟篡改
        conn.commit()
        conn.close()
        rep = collect_outcome_health(db)
        assert rep["integrity_mismatches"] == 1
        assert rep["checks"]["integrity"] is False and rep["passed"] is False

    def test_warnings_for_trace_missing_and_duplicate_hash(self, tmp_path: Path):
        db = _db(tmp_path)
        _insert(db, status="resolved_win", symbol="600519", trace=None)
        dup = {"action": "buy", "same": "snapshot"}
        _insert(db, status="resolved_win", symbol="600520", snapshot=dup)
        _insert(db, status="resolved_loss", symbol="600521", snapshot=dup)
        rep = collect_outcome_health(db)
        assert rep["trace_missing"] == 1
        assert rep["duplicate_snapshot_groups"] == 1
        assert any("snapshot_hash" in w for w in rep["warnings"])

    def test_empty_db_no_rates_and_not_passed(self, tmp_path: Path):
        rep = collect_outcome_health(_db(tmp_path))
        assert rep["total"] == 0
        assert rep["settlement_success_rate"] is None
        assert rep["passed"] is False

    def test_source_type_filters(self, tmp_path: Path):
        db = _db(tmp_path)
        _insert(db, status="resolved_win", symbol="600519", source_type="live")
        _insert(db, status="resolved_loss", symbol="600519", source_type="backtest")
        rep = collect_outcome_health(db, source_type="live")
        assert rep["total"] == 1
        rep_all = collect_outcome_health(db, source_type=None)
        assert rep_all["total"] == 2


class TestCli:
    def test_cli_json_and_exit_code(self, tmp_path: Path, capsys):
        db = _db(tmp_path)
        _insert(db, status="resolved_win", symbol="600519")
        code = main(["--db", str(db), "--source-type", "live"])
        out = json.loads(capsys.readouterr().out)
        assert out["passed"] is True and code == 0

    def test_cli_exit_one_when_failed(self, tmp_path: Path):
        db = _db(tmp_path)
        _insert(db, status="unresolvable", symbol="600519")
        assert main(["--db", str(db), "--source-type", "live"]) == 1
```

- [ ] **Step 2: 跑测确认红**

Run: `uv run pytest tests/evals/outcome/test_health.py -v`
Expected: FAIL（`ModuleNotFoundError` / `cannot import name 'collect_outcome_health'`）

- [ ] **Step 3: 实现 health.py**

```python
"""Outcome 读数健康检查（spec evaluation「Outcome 读数收口纪律」第①步）。

四项读数 + 快照完整性：结算成功率 / 行情缺失率 / 记账完整率 / 污染护栏。
主检查（passed 判据）：结算成功率 ≥0.90、行情缺失率 ≤0.10、integrity 零不一致。
告警（不阻断，供人工终裁入口）：trace 缺失行数、重复 snapshot_hash 组数（incident 031 指纹形态）。
只读：不写 predictions；integrity_check 例外——按既有契约写 integrity_mismatch 审计。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from finance_agent.outcome.track_record.model import integrity_check

MIN_SETTLEMENT_SUCCESS_RATE = 0.90
MAX_MARKET_MISSING_RATE = 0.10


def _db_path(db_path: str | Path | None) -> Path:
    return Path(db_path) if db_path else Path(os.getenv("SESSIONS_DB_PATH", "data/sessions.db"))


def collect_outcome_health(
    db_path: str | Path | None = None,
    *,
    source_type: str | None = "live",
    since: str | None = None,
) -> dict[str, Any]:
    path = _db_path(db_path)
    conn = sqlite3.connect(path, timeout=15.0)
    conn.row_factory = sqlite3.Row
    try:
        where, params = " WHERE 1=1", []
        if source_type:
            where += " AND source_type=?"
            params.append(source_type)
        if since:
            where += " AND created_at>=?"
            params.append(since)
        counts = dict(
            conn.execute(
                f"SELECT status, COUNT(*) FROM predictions{where} GROUP BY status", params
            ).fetchall()
        )
        total = sum(counts.values())
        resolved = sum(counts.get(s, 0) for s in ("resolved_win", "resolved_loss", "resolved_neutral"))
        unresolvable = counts.get("unresolvable", 0)
        settleable = resolved + unresolvable
        success_rate = round(resolved / settleable, 4) if settleable else None
        missing_rate = round(unresolvable / settleable, 4) if settleable else None
        price_missing = int(
            conn.execute(
                f"SELECT COUNT(*) FROM predictions{where} AND entry_price IS NULL", params
            ).fetchone()[0]
        )
        trace_missing = int(
            conn.execute(
                f"SELECT COUNT(*) FROM predictions{where}"
                " AND (langfuse_trace_id IS NULL OR langfuse_trace_id='')",
                params,
            ).fetchone()[0]
        )
        dup_groups = conn.execute(
            f"SELECT snapshot_hash, COUNT(*) c FROM predictions{where}"
            " AND snapshot_hash IS NOT NULL GROUP BY snapshot_hash HAVING c>1",
            params,
        ).fetchall()
    finally:
        conn.close()

    integrity = integrity_check(path)
    checks = {
        "settlement_success": success_rate is not None
        and success_rate >= MIN_SETTLEMENT_SUCCESS_RATE,
        "market_missing": missing_rate is None or missing_rate <= MAX_MARKET_MISSING_RATE,
        "integrity": integrity["mismatch_count"] == 0,
    }
    warnings: list[str] = []
    if trace_missing:
        warnings.append(f"trace 缺失 {trace_missing} 行（非阻断；Langfuse 离线期可预期，异常时人工核对）")
    if dup_groups:
        warnings.append(
            f"重复 snapshot_hash 组 {len(dup_groups)} 个（疑似测试泄漏指纹，须人工核对，incident 031）"
        )
    return {
        "db": str(path),
        "source_type": source_type,
        "since": since,
        "total": total,
        "status_counts": counts,
        "settled_resolved": resolved,
        "unresolvable": unresolvable,
        "settlement_success_rate": success_rate,
        "market_missing_rate": missing_rate,
        "entry_price_missing": price_missing,
        "bookkeeping_completeness": round(1 - price_missing / total, 4) if total else None,
        "integrity_checked": integrity["checked"],
        "integrity_mismatches": integrity["mismatch_count"],
        "trace_missing": trace_missing,
        "duplicate_snapshot_groups": len(dup_groups),
        "checks": checks,
        "warnings": warnings,
        "passed": all(checks.values()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="outcome 读数健康检查（只读）")
    ap.add_argument("--db", default=None)
    ap.add_argument("--source-type", default="live")
    ap.add_argument("--since", default=None)
    args = ap.parse_args(argv)
    report = collect_outcome_health(
        args.db, source_type=args.source_type or None, since=args.since
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测确认绿**

Run: `uv run pytest tests/evals/outcome -v`
Expected: 全 passed（3 + 9 + 8 例）

- [ ] **Step 5: Commit**

```bash
git add evals/outcome/health.py tests/evals/outcome/test_health.py
git commit -m "feat(evals): outcome 健康检查——结算成功率/行情缺失/污染护栏/记账完整率 + CLI（delta add-outcome-profitability-protocol）"
```

---

### Task 5: 验证收口——全量校验 + 人工验证报告 + tasks.md 勾选

**Files:**
- Create: `tests/validation/2026-09-23-add-outcome-profitability-protocol-validation.md`
- Modify: `openspec/changes/add-outcome-profitability-protocol/tasks.md`（勾选 1.x–4.x）

**Interfaces:**
- Consumes: Task 1–4 全部产物

- [ ] **Step 1: 全量相关测试 + lint + 类型**

```bash
uv run pytest tests/evals/outcome tests/evals/causal_ablation -v
uv run ruff check evals/outcome evals/causal_ablation/preregister.py tests/evals/outcome
uv run mypy evals/outcome
```
Expected: pytest 全绿；ruff 零违例；mypy 触碰文件零错误

- [ ] **Step 2: 文档护栏复核（口径条目逐条对照 spec Scenario）**

对照 `openspec/changes/add-outcome-profitability-protocol/specs/evaluation/spec.md` 的三条需求 Scenario，逐条在 metrics.md §1.8 / 预登记文档 / 代码中找到落点，写进验证报告「Scenario 对照表」。

**文档一致性补强（Task 4 审查采纳）**：metrics.md §1.8⑤ 的健康检查括注改为「健康检查（结算成功率 ≥0.90 / 行情缺失率 ≤0.10 / integrity 零不一致 / 记账完整率——读数不设阈值）」；并在同格补「integrity 为全库扫描（不受 source_type/since 过滤），其余读数按过滤口径（`integrity_scope` 字段随报告披露）」。改后重跑 `uv run pytest tests/evals/outcome/test_caliber_doc.py -v` 确认护栏测试仍绿。

- [ ] **Step 3: 写人工验证报告**

`tests/validation/2026-09-23-add-outcome-profitability-protocol-validation.md`，模板：

```markdown
# 人工验证报告: add-outcome-profitability-protocol

**日期**: 2026-09-23
**验证人**: agent（机器项）+ owner（口径确认项）
**关联 delta**: openspec/changes/add-outcome-profitability-protocol/
**E2E 门禁**: 不适用（非交互类变更，零前端/SSE/状态流转改动）

## Scenario 对照表
（三条需求的全部 Scenario → 落点文件/行 + 证据）

## 机器验证结果
（测试输出摘要 / ruff / mypy）

## Owner 决策点确认
- 主窗口 T+20：**默认接受**（「开始」指令）；如变更走 metrics.md §1 修订 + 新预登记版本
- 泄漏探针阈值 0.60 / 两腿预算：默认接受，Δ3/Δ4 实施前可再次裁决

## 异常记录
（无 / 逐条）

## 结论
[ ] 全部通过（可进 sync/archive 流程）
```

- [ ] **Step 4: 勾选 openspec tasks.md**

`openspec/changes/add-outcome-profitability-protocol/tasks.md`：1.1–1.3、2.1–2.2、3.1–3.3、4.1–4.2 全部勾选；4.3 注明「validate 已过；sync/archive 待读数腿落地后统一（口径先行，不随本 delta 单独 archive）」。

- [ ] **Step 5: openspec validate + Commit**

```bash
openspec validate add-outcome-profitability-protocol --strict
git add tests/validation/2026-09-23-add-outcome-profitability-protocol-validation.md openspec/changes/add-outcome-profitability-protocol/tasks.md
git commit -m "test(evals): add-outcome-profitability-protocol 验证收口——Scenario 对照 + owner 决策点确认（tasks 勾选）"
```

---

## Self-Review（计划自审）

- **Spec 覆盖**：需求①口径与预登记 → Task 1（§1.8）+ Task 2（门禁与文档）+ Task 3（caliber/结论）；需求②收口纪律 → Task 3（结论/报告）+ Task 4（健康检查）+ Task 5（对照表）；需求③双腿互证 → 口径在 §1.8⑥（Task 1）、两腿实现分别在 delta 3/4（不在本计划范围，proposal 已声明依赖）。无缺口。
- **占位符扫描**：无 TBD/TODO；所有步骤含完整代码或完整文档文本。
- **类型一致性**：`collect_outcome_health` 返回键在 Task 4 测试与实现一致；`conclude_outcome` 签名在测试/实现一致；`OUTCOME_REQUIRED_FIELDS` 在 Task 2 测试与实现一致。
- **已知依赖**：Task 4 测试依赖 `finance_agent.outcome.track_record.model`（生产代码，只读导入，零改动）。
