## 1. 库层：rejected 状态与拒绝池读写

- [x] 1.1 TDD 红：`_TRANSITIONS` 接纳 `rejected` 终态、`add_factor` 接受 `rejected` 初始状态、非法迁出被拒（先写失败测试）
- [x] 1.2 TDD 绿：状态机与 `add_factor` 扩展（`library.py`）
- [x] 1.3 TDD 红→绿：`list_factors_by_status`、`find_rejected_duplicate`（同构比对复用规范串快路径）、`record_rejected_factor`（新建行+created 事件 / 同构命中 upsert+追加事件）
- [x] 1.4 审计并修 `FROM factors` 全部读路径：只读列表排除 `rejected`、分桶保种/池增量/打分不含 `rejected`（行为不变性测试）

## 2. 准入编排：拒收持久化单点

- [x] 2.1 TDD 红：`admit_candidates` 各拒收点（闸①②③④⑤+批内二次查重）产出 `rejected` 行与事件（detail 含闸口/原因/读数）
- [x] 2.2 TDD 绿：`admit_candidates` 接线 `record_rejected_factor`（单点，三入口自动生效）
- [x] 2.3 TDD 红→绿：同批逐字重复 / 跨批同构重复拒收 upsert 不重复插入；账本 `factor_id` 口径不变（拒收行 NULL）
- [x] 2.4 TDD 红→绿：builtin 注册跳过判定排除 `rejected` 行（被拒 builtin 次日可重试）

## 3. 监控日批：拒绝池 IC 跟踪

- [x] 3.1 TDD 红：`run_factor_monitor` 为 `rejected` 行缺口回填记 IC（同 active 口径、幂等）
- [x] 3.2 TDD 绿：monitor 扩扫接线（无衰减判定、无状态迁移）
- [x] 3.3 TDD 红→绿：摘要新增 `rejected_checked` / `rejected_recorded` / `rejected_days_recorded`，既有字段语义回归测试

## 4. 存量回填

- [x] 4.1 TDD 红→绿：`backfill_rejected_from_state`（原裁决原样导入、无 DSL 跳过、缺 `ast_json` 现算、幂等零新增）
- [x] 4.2 `scripts/backfill_rejected_factors.py` 薄 CLI（`--state`/`--db` 显式必填，无默认路径）

## 5. 收尾验证

- [x] 5.1 定向测试全绿（tests/factors/）+ 全量后端测试无回归 + `ruff check` / `ruff format` / `mypy` 干净
- [x] 5.2 delta `validate --strict` 通过；提案/设计/任务与实现一致性核对
- [x] 5.3 PR 描述含验证读数；合并部署后：跑回填脚本（显式路径）+ 次日衰减监控摘要含拒绝池读数人工核对
