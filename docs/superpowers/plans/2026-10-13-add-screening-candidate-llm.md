# add-screening-candidate-llm Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** L3 LLM 排雷粗筛（quick profile、合同化 keep/drop + reason ≤50 字、并发执行、单只失败降级 keep）+ 在榜稳定性门槛（连续 N=3 交易日在 L2 Top-N，断档重新计数）+ 决策与 L2 名单落库。

**Architecture:** `screening/candidate_llm.py`（纯组装 + 注入 client 的执行器，ThreadPoolExecutor 并发，结果按候选序确定性归位）；`screening/store.py`（SQLite：L2 名单日快照 + triage 决策，稳定性门槛查询的唯一数据源）；prompt 模板 `prompts/screening_triage.md`（权威源，部署同既有 deploy_prompts 流程）。事件数据由调用方注入（`events_by_symbol`），本 change 不自行取数——proposal 中「复用 events/ 模块」按实际代码修正为「事件列表参数注入，生产由 pipeline 从公告/新闻源装配」（写进 docstring 与报告）。

**Tech Stack:** Python 3.14 / concurrent.futures / sqlite3 / 既有 `factors/propose.py` 的 LLM client seam / pytest。

## Global Constraints

- 测试：`uv run pytest tests/screening -q`；ruff/mypy 同仓库纪律；TDD 红→绿；既有测试零修改。
- 合同逐字：`verdict ∈ {keep, drop}`；`reason` ≤ 50 字（中文按字符数，去首尾空白；缺失或超长 → 合同违约 → 重试 1 次后仍违约 → **按 keep 降级**并记 warning（宁可多留不可错杀，与失败降级同路））。
- 并发上限默认 8（可配置 `max_workers`）；结果顺序 = 输入候选顺序（并发不改变确定性）。
- 稳定性门槛默认 `min_days=3`（可配置）；断档重新计数（spec 场景：1/2 在榜、3 出榜、4/5 在榜 → 第 5 日计数 = 2）。
- 库文件：`SCREENING_DB_PATH`（默认 `data/screening.db`），连接纪律同 `data/snapshot.py`。
- 零网络单测（stub client）；live 真跑（真 LLM 抽检）为 Task 3 的验证步骤，失败如实记录。

## 关键设计决策

**C1 — client seam**：复用 `factors/propose.py` 的 `ProposalLLMClient` 形状（`complete_json(prompt, *, purpose, metadata) -> dict`），但 purpose 固定 `"quick"`（成本纪律，spec 明文 quick profile）；本模块定义 `TriageLLMClient` Protocol（结构相同），默认实现包 gateway（`purpose="quick"`），stub 可直接替换。

**C2 — prompt 组装**：`build_triage_prompt(candidate: Candidate, events: Sequence[dict]) -> str`：渲染 `prompts/screening_triage.md` 模板，注入综合分、`factor_contribs` 摘要（factor_id/weight/contribution 逐条）、事件列表（每条 `title/date/type` 或原始 dict 的 json 摘要）；事件为空 → 模板给出「无最近事件」占位。纯函数，可单测断言关键字段出现。

**C3 — 失败与违约降级**：任一只的调用抛异常或两次合同违约 → 该只 `verdict="keep", degraded=True, reason="triage 失败，按 keep 降级"`（固定文案），进入 `warnings`（含 symbol + 错误类型）；**不阻断批次**。spec 场景（50 只中 1 只抛 typed error）→ 其余 49 只正常、失败者 keep、trace 含 warning。

**C4 — 稳定性计数**：`consecutive_days_in_top(symbol, as_of, *, db_path=None) -> int`：从 `as_of` 起向前逐交易日查 `l2_candidates`（自然日回溯时跳过非交易日由数据本身决定——表里只有交易日行）；`as_of` 不在榜 → 0；返回尾部连续段长度。`eligible_for_selection(symbol, as_of, *, min_days=3, db_path=None) -> bool` = `consecutive_days_in_top(...) >= min_days`。

**C5 — 落库**：`l2_candidates(trade_date, symbol, composite_score, rank, PRIMARY KEY(trade_date, symbol))`（幂等 upsert，同日重跑覆盖）；`triage_decisions(trade_date, symbol, verdict, reason, degraded, created_at, PRIMARY KEY(trade_date, symbol))`（幂等 upsert）。两表供消融/命中率分层（后续 change）查询。

## 文件结构

| 文件 | 职责 |
|---|---|
| `src/finance_agent/screening/candidate_llm.py`（新） | 合同、prompt 组装、并发执行、降级 |
| `src/finance_agent/screening/store.py`（新） | screening 库：L2 名单 + triage 决策 + 稳定性查询 |
| `src/finance_agent/prompts/screening_triage.md`（新） | triage prompt 模板 |
| `tests/screening/test_candidate_llm.py`、`test_screening_store.py`（新） | 单测 |
| `tests/validation/2026-10-13-add-screening-candidate-llm-*.py/.md`（新） | live 抽检 + 报告 |

## Interfaces（逐字）

```python
# candidate_llm.py
class TriageLLMClient(Protocol):
    def complete_json(self, prompt: str, *, purpose: str, metadata: dict) -> dict: ...
@dataclass(frozen=True) class TriageDecision:
    symbol: str; verdict: str; reason: str; degraded: bool = False
@dataclass(frozen=True) class TriageBatchResult:
    decisions: tuple[TriageDecision, ...]; warnings: tuple[dict, ...]; trace_metadata: dict
def validate_triage_payload(payload: dict) -> tuple[tuple[str, str] | None, str | None]   # (verdict, reason) | error
def build_triage_prompt(candidate: Candidate, events: Sequence[dict]) -> str
def run_triage(candidates: Sequence[Candidate], *, events_by_symbol: dict[str, list[dict]],
               client: TriageLLMClient, trade_date: str, max_workers: int = 8,
               db_path=None) -> TriageBatchResult
    # 并发执行 + 合同校验（违约重试 1 次）→ 降级 keep → 落库 → 返回（顺序 = 输入序）

# store.py
def init_screening_db(db_path=None) -> Path
def record_l2_candidates(trade_date: str, candidates: Sequence[Candidate], db_path=None) -> int
def list_l2_candidates(trade_date: str, db_path=None) -> list[dict]
def record_triage_decision(trade_date: str, decision: TriageDecision, db_path=None) -> None
def list_triage_decisions(*, trade_date: str | None = None, symbol: str | None = None, db_path=None) -> list[dict]
def consecutive_days_in_top(symbol: str, as_of: str, *, db_path=None) -> int
def eligible_for_selection(symbol: str, as_of: str, *, min_days: int = 3, db_path=None) -> bool
```

---

### Task 1: 合同 + prompt + 并发执行

- [ ] **Step 1: 写失败测试**（stub client）：合法 payload → decision；verdict 非法/reason 缺失/reason 51 字 → 违约 → 重试 1 次（stub 记录调用数）→ 仍违约 → keep+degraded+warning；**单只抛 typed error**（50 只中 1 只）→ 其余 49 正常、失败者 keep、warnings 含 symbol；**并发上限**（stub 记录同时在飞数量 ≤ max_workers；用 sleep 桩验证）；**结果顺序 = 输入序**（并发完成顺序打乱时仍按输入序）；prompt 组装含综合分/贡献摘要/事件标题；事件为空 → 占位文案；落库调用（stub db 断言每只一行）。
- [ ] **Step 2–4: 红 → 实现 → 绿**（C1–C3/C5 决策部分）。
- [ ] **Step 5: Commit** `feat(screening): L3 LLM triage with contract, concurrency and keep-fallback (T1)`。

### Task 2: screening 库 + 稳定性门槛

- [ ] **Step 1: 写失败测试**：两表 DDL 幂等；`record_l2_candidates` 幂等 upsert（同日重跑行数不变）；`consecutive_days_in_top`：连续 2/3/4 日、**断档重新计数**（spec 场景：1,2 在榜 → 3 出榜 → 4,5 在榜，第 5 日计数 = 2）、`as_of` 不在榜 → 0、跨非交易日（表内无行）不打断；`eligible_for_selection` 边界（2 日 False / 3 日 True / min_days 可配置）；`list_triage_decisions` 过滤。
- [ ] **Step 2–4: 红 → 实现 → 绿**（C4/C5）。
- [ ] **Step 5: Commit** `feat(screening): screening store + listing-stability threshold (T2)`。

### Task 3: live 抽检 + 收口

- [ ] live 真跑（有 LLM 凭据才跑，否则如实记录受阻）：构造 ~25 只合成候选（含 5 只注入风险事件：立案/大额减持/业绩预亏；其余注入中性事件），经默认 client（quick profile）真跑 triage → 统计 keep/drop 分布、reason 长度全部 ≤50、5 只风险候选的 drop 率；**抽 20 条 drop/keep 理由逐条记录**（原始 prompt 摘要 + verdict + reason）供人工终审。
  - 诚实注记：live 2026-09 真实候选不可得（东财 IP 封禁 / issue #325）→ 合成候选 + 注入事件替代；真跑验证的是「合同与 LLM 行为」，不是真实市场有效性。
- [ ] prompt 部署尝试（Langfuse 离线 → 如实记「待部署」）。
- [ ] `uv run pytest tests/screening -q` + 全量 `tests/ -m "not live"`（记录）；ruff/mypy。
- [ ] 报告 + `tasks.md` 全勾 →（控制器 archive）。Commit `docs(openspec): add-screening-candidate-llm validation report + tasks 回填`。
