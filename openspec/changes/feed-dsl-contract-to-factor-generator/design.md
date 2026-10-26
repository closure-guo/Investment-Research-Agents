# Design: feed-dsl-contract-to-factor-generator

## Approach

数据驱动、单一真相源、fail-safe 三原则：

1. **宿主侧**（`src/finance_agent/factors/feedback.py`）：`build_payload` 新增 `contract` 块与 `gate_rejections` 聚合。契约数值 **import 取值**（`from finance_agent.factors.dsl import MAX_AST_DEPTH, MAX_FREE_PARAMS, MAX_FIELDS`；`from finance_agent.factors.qlib_translate import OPERATOR_MAPPINGS, FIELD_MAPPINGS`），不复制字面量——上游调阈值/扩算子时契约自动跟随。规则文本为模块常量（英文，与消费端 prompt 语言一致），三条对应实测三大死因（ε 符号、横截面、矩阵/ML）。
2. **消费端**（`tests/scripts/spike/explored_feedback.py`，部署件）：`render_explored_block` 先渲染契约段（从载荷 `contract` 字段数据驱动拼装，不硬编码约束文本），再渲染已探索清单；两者共享 `MAX_CHARS` 预算（契约段自带上限，剩余预算给清单）。载荷无 `contract` → 行为与现状逐字相同。
3. **部署**：消费端源码经 wsl 覆盖 distro site-packages（补丁脚本 `patch-explored-feedback.py` 的幂等标记不受影响，无需重打）；运行中的 rdagent 进程在下次进程启动时生效。宿主侧容器重建前，日批 15:55 的自动导出仍写旧版载荷（无契约块）——用 `scripts/export_factor_feedback.py` 手动导出一次即可让当日研究循环看到契约。

## Alternatives Considered

- **放宽复杂度上限 / 转译侧约定 ε 缺省值**：前者违背 2026-10-19 治理裁决（PR #410：自由参数维持 3，防过拟合是闸门设计初衷）；后者动转译等价性契约（"禁止猜测"），语义滑坡。均不选——本变更只让生成侧在约束内工作，闸门与契约零改动。
- **把规则硬编码进消费端**：阈值/白名单演进时会与宿主漂移（消费端在 distro、更新易漏）。数据驱动从载荷读取，宿主单一真相源自动流经。
- **新增独立 distro 部署脚本**：现有 patch 脚本头部已文档化复制流程，且实际部署是单文件覆盖；沿用既有流程，design 留档即可。

## Risks

- **prompt 预算挤占**：契约段约 1.5–2k 字符，清单预算相应减少（现状 9000 上限内）。已探索清单当前 80 条 × ~150 字符本就常触截断——去重指引是"尽量别重复"，契约是"别产出结构性废案"，后者单位字符价值更高；截断标注保留可观测性。
- **生成侧遵从性不确定**：LLM 可能仍违规（规则只是软约束）。验收看趋势：untranslatable 占比与三大死因桶的规模（观察指标进 `factor-research` 页与收割摘要，不新立指标）。
- **容器重建时窗**：日批 15:55 自动导出在容器重建前不带契约块，窗口期内手动导出可补；重建须避开红线时窗并做前置检查（AGENTS.md 红线）。
