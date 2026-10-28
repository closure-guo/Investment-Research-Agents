# Design — add-rejected-factor-pool

## 技术方案

### 1. 状态机与库层（`factors/library.py`）

- `_TRANSITIONS` 增加 `"rejected"` 为**终态**（`frozenset()`）：拒绝池行永不参与状态迁移（复苏走未来的重评 delta，产生新行，不改写历史）。`add_factor` 的合法初始状态集合增加 `"rejected"`。
- `list_factors` 增加 `include_rejected: bool = False`：缺省排除 `rejected` 行（拒绝池对既有读路径不可见，一处改动覆盖 api/monthly/builtin 全部消费方）；`status` 显式给定时精确返回（拒绝池跟踪走 `status="rejected"`）。实施时以此替代原计划的独立 `list_factors_by_status`——枚举能力本就存在，无需新函数。
- 新增 `find_rejected_duplicate(ast_json, db_path)`：仅在 `status='rejected'` 行内做与 `find_structural_duplicate` 同款的同构比对（复用规范串精确命中快路径 + 全量同构扫描）。**不改** `find_structural_duplicate` 与 `_DEDUP_STATES`——查重范围维持 `candidate/active/degraded`，`rejected` 天然不在其中。
- 新增 `record_rejected_factor(expression, hypothesis, category, ast_json, source, gate, reason, detail, db_path)`：命中同构 rejected 行 → `UPDATE updated_at` + 追加事件（`event='rejected_again'`？不——事件词表收紧，复用 `status_change` 不合适；新增事件类型 `EVENT_RE_REJECTED = "re_rejected"`）；未命中 → `add_factor(status='rejected')` + created 事件（`detail={"gate": N, "reason": ..., **判定读数}`）。

### 2. 准入编排（`factors/validate.py`）

- `admit_candidates` 阶段 A（逐候选，闸①②拒收点）、阶段 B（闸③拒收点）、阶段 C（闸④⑤拒收点与批内二次查重拒收点）各拒收分支在写 `outcomes[position]` 的同时调用 `record_rejected_factor(...)`，入参取 `_PendingCandidate` 已有字段 + 命中闸口的 `GateResult`（`reason` / `detail`）。预检失败（`external._preflight`，无 DSL）不经过此路径，天然不落库。
- 账本 `_record_ledger` **不动**（rejected 行不回填 ledger.factor_id，维持"入库行才带 factor_id"的既有口径）。
- 幂等：收割重放 / 手动补跑重复评估同表达式时，upsert 语义保证不重复插入。

### 3. builtin 跳过判定（`factors/builtin.py`）

- 现逻辑"库内已存在同表达式条目（任意状态）即跳过"改为显式状态集合 `candidate/active/degraded/retired`（即**排除** `rejected`）：被拒的内置因子次日日批仍会重试。规范场景钉死此点。

### 4. 监控日批（`factors/monitor.py`）

- `run_factor_decay_monitor` 在 active 循环之后追加拒绝池循环：`list_factors_by_status("rejected")` 逐行 `_record_missing_ic`（与 active 完全同口径：面板窗口内可算且尚缺的交易日，幂等 upsert），**不做** `_decay_snapshot` / `set_status`。
- 摘要新增 `rejected_checked` / `rejected_recorded` / `rejected_days_recorded`；既有字段语义不变。面板校验（`as_of` 在面板内、`close` 字段）沿用函数入口既有断言。

### 5. 存量回填（`factors/reject_backfill.py` + `scripts/backfill_rejected_factors.py`）

- 核心逻辑入库内（可测试）：`backfill_rejected_from_state(state_path, db_path) -> {imported, updated, skipped_no_expression, skipped_other}`——读状态文件 `processed` 映射，取 `status=='rejected'` 且带 `expression`（DSL）与 `ast_json` 的条目，按原裁决（记录中的 gate/reason/detail）调 `record_rejected_factor` 原样导入；`untranslatable` / `transcription_error` / 无表达式条目跳过。**不重新过闸**。
- `scripts/backfill_rejected_factors.py` 为薄 CLI（`--state` / `--db` 显式必填，无默认路径——MUST NOT 隐式碰生产库），部署后人工执行一次；重复执行零新增（幂等由 upsert 语义保证）。
- 注意状态文件条目的 `ast_json` 缺失场景：旧数据可能只有 `expression`——回填时按 `serialize(parse(expression))` 现算 `ast_json`（与 `find_structural_duplicate` 的回退逻辑一致）。

### 6. 只读 API 防泄漏

- 审计 `api.py` / `ops_api.py` 因子库读路径：列表若存在非状态过滤的 `SELECT ... FROM factors`，补 `status != 'rejected'` 过滤（对外行为不变——当前库内无 rejected 行，过滤后返回集与现状一致）。详情端点按 id 查询不挡（拒绝池条目可被 id 直查，属只读诊断）。

## 关键取舍

- **rejected 入 `factors` 表而非新表**：复用 DDL/事件表/`factor_ic`/`record_ic`/`reset_factor_library`，IC 跟踪零新表；代价是需审计全部 `FROM factors` 查询防泄漏（方案 §6）。独立表则要复制事件与 IC 两套管道，不值。
- **upsert 而非追加行**：同构重复拒收保留单行单身份，IC 序列不因重提被切碎；历史裁决经事件表保留（每次拒收一条事件）。
- **闸①（可计算性）拒收也持久化**：表达式合法但面板上缺失率过高——表达式与 `ast_json` 都在，未来换面板窗口有复评价值。
- **不做 IC 复苏触发重评**（scope 外）：本 change 只保证"看得见"（持久化+IC 序列）；"何时重评"留给后续 delta，避免一个 change 同时动裁决面与观测面。
- **性能**：rejected 行量级数百，逐条面板求值预计分钟级增量，落在 17:55–20:45 日批窗口内；配额治理（是否只跟踪近期/高价值拒绝）留后续 delta。

## 风险与边界

- **与在飞 change 的规范冲突**：`add-analyst-revision-factors` 也改 `factor-library`（其 delta 为 ADDED 的分析师修正因子需求，与本 change 的三个新 requirement 命名不相交），archive 顺序无强约束；若届时 archive 撞 delta 漂移，按既有解法对齐真实 requirement 全文。
- **测试隔离**：factors 测试全部走 tmp 库（既有惯例），不触生产 `data/factors.db`；实施在 worktree 进行，不碰主检出。
- **监控耗时回退位**：若实跑发现拒绝池循环显著拉长日批，先按闸口/时间窗收窄跟踪范围（纯读侧参数），不动写入侧。
