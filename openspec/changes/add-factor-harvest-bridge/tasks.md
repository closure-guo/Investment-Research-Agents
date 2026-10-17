# Tasks: add-factor-harvest-bridge

> 上线顺序 = 任务顺序。TDD：先红后绿。

## T1 采集器：因子块 → 候选文件（tests/scripts/spike/collect_factor_status.py）

- [x] 先红后绿：`extract_factor_blocks`（三元组抽取、半块不产出、去重保序）
- [x] 先红后绿：`collect_once` 写 `candidates.json`（缺省与状态文件同目录；全局去重跨周期/跨会话；
      增量重叠窗防块截断）

## T2 翻译器入生产（tests/scripts/spike/qlib_translate.py → src/finance_agent/factors/）

- [x] `git mv` + 导入面更新（spike 运行器与测试改包路径）；30 用例全绿

## T3 收割模块 + prompt（factors/harvest.py + prompts/factor_transcription.md）

- [x] 先红后绿：载荷合同（二值裁决；缺 reason/双空拒收）；prompt 组装（因子块注入）
- [x] 先红后绿：端到端三态混合（可翻译过真实闸门 + LLM 拒译 + 翻译器拒译 → errors 桶；
      拒收记录含读数 detail）；幂等（二次零 LLM 调用）；网关故障 fail loud（不写状态）；批上限
- [x] purpose `factor_transcription`（types/router）+ prompt 发布 Langfuse

## T4 注册表 + 拒收读数

- [x] `factor_harvest_daily`（工作日 15:55；JOB_IDS/LABELS/FUNCS/SCHEDULES 四表登记）
- [x] 拒收载荷携带 `detail`（external.py + `_rejected_gate_detail` 单源）

## T5 实跑与证据

- [x] 首次真实收割（20 条候选，真实 LLM + 生产库路径）：11 可转译 / 8 不可翻译 / 1 转译失败；
      过闸 0 入库（闸②为主，读数 0.013–0.229 已入拒收 detail）；状态幂等回填
- [x] 验证报告 `tests/validation/2026-10-17-add-factor-harvest-bridge-validation.md`

## 收口

- [x] `uv run pytest tests/factors tests/llm tests/test_ops_api.py tests/evals/test_run.py -q` 全绿（781）；
      ruff / mypy 零新增
- [x] `openspec validate add-factor-harvest-bridge --strict` 通过
- [x] 验证报告 + PR；sync + archive 在验证通过后执行
