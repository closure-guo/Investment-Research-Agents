# Tasks: add-screening-candidate-llm

## 1. 粗筛节点
- [x] 1.1 实现 candidate_llm.py（quick profile + 输出合同 keep/drop+reason）+ stub 单测
      —— `candidate_llm.py`（C1 协议/GatewayTriageClient purpose 钉死 quick；C2 prompt 组装；
      C3 合同 verdict ∈ {keep, drop}、reason ≤ 50 字去首尾空白）+ `prompts/screening_triage.md`；
      单测 `tests/screening/test_candidate_llm.py` 20 例（stub client 零网络；50/51 字边界、
      违约重试计数、事件渲染/空事件占位）。Commit `00aa602f`。
- [x] 1.2 并发执行与单只失败降级 keep + 并发与降级单测
      —— ThreadPoolExecutor（默认 max_workers=8 可配置）；单只抛 typed error（50 只中 1 只）
      其余 49 只正常、失败者 keep+degraded+warning；违约重试 1 次仍违约同路降级；
      sleep 桩验证并发峰值 ≤ 上限、完成序倒置仍按输入序；trace metadata 含 warnings。Commit `00aa602f`。

## 2. 稳定性门槛
- [x] 2.1 连续 3 日在榜门槛 + 边界单测（2 日不通过、3 日通过、中间断档重新计数）
      —— `store.consecutive_days_in_top`（沿表内交易日轴自 as_of 向前计数；非交易日无行不打断）
      + `eligible_for_selection`（min_days 默认 3、可配置）；spec 断档场景 1/2 在榜 → 3 出榜 →
      4/5 在榜，第 5 日计数 = 2 已钉死。Commit `8258e38d`。

## 3. 决策落库
- [x] 3.1 keep/drop 决策落库（供消融与命中率分层）+ 落库单测
      —— `triage_decisions` 表幂等 upsert（每只一行含降级 keep；写入路径 Commit `00aa602f`，
      过滤读取/同日覆盖 Commit `8258e38d`）；另落 `l2_candidates` 日快照表（同日重跑幂等）
      为稳定性门槛查询唯一数据源。live 抽检 DB 回读 25/25 行（见 4.2 报告）。

## 4. 验证
- [x] 4.1 全量测试通过 —— 见验证报告 §0.1（`tests/screening` + 全量 `-m "not live"` 逐字记录；
      唯一失败为既有 llm env flake，归因见报告 §2）。
- [x] 4.2 一周候选 L3 抽 20 条 drop 理由人工核对，报告落 `tests/validation/`
      —— **诚实注记**：2026-09 真实候选不可得（东财行情域 IP 封禁，incident 033 / issue #325）
      → 以 25 只合成候选（5 风险：立案×2/大额减持×2/业绩预亏×1 + 20 中性）注入事件真跑替代；
      5 风险全 drop、20 中性全 keep、reason 最长 41 字（≤50）、25/25 落库回读；
      20 条理由（5 风险 + 前 15 中性固定抽样）逐条含 prompt 摘要落 JSON 供人工终审
      （`tests/validation/2026-10-13-add-screening-candidate-llm-live*.py/.json`）。
      验证的是输出合同与 LLM 行为，**不构成真实市场有效性证据**。
- [x] 4.3 既有测试无修改通过（红线）—— 例外仅两处，且均为评审 finding 明文要求：
      `tests/screening/test_ranker.py` 常量序列用例**加强**（新增 `[0.1,0.1,0.1]` 反例，
      先红后绿钉死 5.88e15 漏检）、`tests/factors/test_builtin.py` 两处**docstring 与断言对齐**；
      其余既有测试零修改（新功能全部为新增文件）。
