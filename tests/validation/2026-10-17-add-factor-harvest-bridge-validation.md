# 因子收割桥验证报告（add-factor-harvest-bridge）

- 日期：2026-10-17（实现/单测 17:3x；prompt 发布 17:38；首次真实收割 17:46）
- 变更：`openspec/changes/add-factor-harvest-bridge`（供给线最后一段：研发产出 → 转译 → 同一五道闸，全自动日批）
- 链路：distro 采集器（`candidates.json` 因子块）→ LLM 转译（Qlib 表达式，二值裁决）→ 确定性翻译
  （`qlib_translate` 映射表）→ `run_factor_external_import`（五道闸，`source="external"`）→ 状态幂等回填

## 1. 测试证据

| 层 | 结果 |
|---|---|
| 采集器因子块/候选文件（新增 6 用例） | 25/25 |
| 收割模块（载荷合同/三态端到端含真实闸门/幂等/fail loud/批上限，13 用例） | 13/13 |
| 翻译器入生产（导入面更新） | 30/30 |
| 消费者回归（factors + llm + ops API + evals） | **781 passed**；ruff / mypy 干净 |
| **变异抽验（测试咬合力）** | 3/3 killed：幂等过滤移除 / 拒收 detail 置空 / untranslatable 缺 reason 放行——各自对应测试立即变红 |

## 2. 首次真实收割读数（20 条真实候选，真实 LLM + 生产库路径）

- **转译三态**：可转译 **11** / 不可翻译 **8** / 转译失败 **1**（LLM 响应非法 JSON → 留痕
  `transcription_error`）。
- **过闸：0 入库**（11 条全部被拒）：闸② WEAK_ICIR ×9、闸① LOW_COVERAGE ×1、闸④
  NO_POOL_INCREMENT ×1——**拒收记录已携带判定读数**：|ICIR| 实测 0.013–0.229；LOW_COVERAGE 的
  `missing_rate=0.4434`（60 日窗因子：60/150 热身 40% + 面板基线）。
- **幂等**：二次运行 `proposed=0`、零 LLM 调用 ✓。

**不可翻译归因（8 条，逐条留 blocker）**：4 条为负数常量形态（`-1 * (…)`——翻译器 v1 保守拒绝，
blocker 已注明「可用 sub 重排表达」）；其余为滚动极值/偏度等算子缺口与深度/参数越界。
→ 后续项（各属独立评估）：负数常量改写规则、滚动 Max/Min 算子线。

## 3. 注册表与运行面

- `factor_harvest_daily`（工作日 15:55，日批链：快照 15:30 → 选股 15:40 → 衰减 15:45 →
  内置注册 15:50 → **收割 15:55** → 结算链 16:00 起）；运行历史/补跑/状态页沿用既有基建
  （ops API 测试对注册表为动态断言，已随套件绿）。
- prompt `factor_transcription` 已发布 Langfuse production；purpose `factor_transcription`
  （JSON 能力硬要求）已入 PolicyRouter。

## 4. 边界与异常（诚实记录）

- **首次运行失败归因（非模块缺陷）**：一次性手工调用漏 `load_dotenv('.env')` → 走 registry 默认
  deepseek-official profile + shell 中陈旧的 `DEEPSEEK_API_KEY` → 401。**容器内日批（compose env）
  与 `api.py`（import 期 load_dotenv）均无此问题**；本地手工跑收割须先 `load_dotenv`——记入运维纪律。
- LLM 转译的语义漂移无法被闸门直接识别（闸门测表达式本身）——缓解：原始 formulation 与转译链
  随 hypothesis 留档、prompt 拒译纪律 + 确定性翻译层 blocker 双重拦截（本次实测即由该层拦下
  4 条形态不可表达的转译产物）。
- 全自动过闸 = 既有设计立场（外部结论不替代闸口读数；月批提案本就全自动）；裁判未换、阈值未动。

## 结论

- [x] 收割桥全链可用（采集器 → 转译 → 翻译 → 五道闸 → 幂等状态）；781 回归绿；变异 3/3
- [x] 首次真实收割读数落档（0 入库、闸②为主、读数可判读）；日批 15:55 全自动接管
- [x] 后续项明确：负数常量改写 / 滚动 Max/Min / 准入面板深度（回填中，独立 delta）
