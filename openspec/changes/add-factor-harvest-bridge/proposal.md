# 因子收割桥：RD-Agent 产出 → 转译 → 同一五道闸（add-factor-harvest-bridge）

## Why

供给线最后一段手工路：翻译覆盖已收口到 91%（29/32，`extend-factor-dsl-coverage`），但「研发产出 →
入库」仍靠**人工逐条转写**（10-16/10-17 两批候选均为 agent 手工转写）——「不说停就一直研发」的
持续产出没有持续入口。本 delta 补上收割桥：**全自动日批**，候选源 = 采集器已写出的 RD-Agent
因子块（name/description/formulation，`candidates.json`），转译 + 翻译 + 五道闸一条链，
闸门即裁判（全自动过闸 = 既有设计立场：外部结论不替代闸口读数）。

## What Changes

- **MODIFIED「外部研发供给接入」（factor-proposal）**：新增**自动收割通道**（日批）——候选文件 →
  LLM 辅助转译 → 确定性翻译 → 与外部导入同一五道闸（`source="external"`）；已处理候选记状态
  （幂等不重复）；三态留痕（转译失败/不可翻译/预检失败 → `errors`，单条不阻断整批）；网关不可用
  fail loud；**拒收记录携带闸口判定读数**（detail：missing_rate/icir 等，拒收可判读）。
- **MODIFIED「外部表达式翻译契约」（factor-proposal）**：翻译器支持两类输入形态——① Qlib Alpha
  表达式（确定性逐算子映射，原契约不变）；② RD-Agent 原生形态（formulation/描述，LLM 辅助转译为
  Qlib 表达式后再经 ①）；LLM 载荷合同 = 合法 Qlib 表达式 XOR 显式 `untranslatable`+reason
  （MUST NOT 猜测）；原始 formulation 与转译链（qlib → dsl）随候选 hypothesis 留档。
- **实现面**：采集器（distro 侧）增量抽取因子块 → `candidates.json`（全局去重、跨会话不丢）；
  翻译器 `qlib_translate` 从 spike 目录**入生产**（`factors/qlib_translate.py`，映射表可审计）；
  新模块 `factors/harvest.py`（转译客户端协议 + 载荷校验 + 状态幂等 + 批入口）；新 prompt
  `factor_transcription.md`（+发布）；ops 注册表新增 `factor_harvest_daily`（工作日 15:55，全自动）。

## Impact

- 代码：`factors/harvest.py`（新）、`factors/qlib_translate.py`（spike → 生产）、
  `factors/external.py` + `monthly.py`（拒收 detail）、`outcome/ops/jobs.py`（注册表）、
  `llm/types.py` + `llm/router.py`（purpose `factor_transcription`）、`prompts/factor_transcription.md`、
  `tests/scripts/spike/collect_factor_status.py`（因子块抽取）。
- 测试：`tests/factors/test_harvest.py`（13 用例：载荷合同/提示组装/端到端三态混合含真实闸门/
  幂等/fail loud/批上限）、采集器 +6 用例、翻译器测试导入更新。
- **不做（明确边界，后续项）**：负数常量改写（`-1 * X` → `sub(0,X)`，翻译器 v1 保守拒绝、
  blocker 已注明可重排——本次实测 4 条候选仅因此被拒，独立评估）；滚动 Max/Min 算子线；
  准入面板深度（独立 delta，回填完成后实施）。
- 实跑读数：首次真实收割（20 条候选）→ 11 可转译 / 8 不可翻译 / 1 转译失败；过闸 0 入库
  （闸②为主，读数 0.013–0.229）；报告落 `tests/validation/`。
