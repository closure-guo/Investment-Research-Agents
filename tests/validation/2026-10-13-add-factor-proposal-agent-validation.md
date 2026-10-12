# 人工验证报告: add-factor-proposal-agent

**日期**: 2026-10-13
**验证人**: ZCode 实施/验证 runner（自动化执行本清单；人工终审签字待补）
**关联 delta**: `openspec/changes/add-factor-proposal-agent/`
**执行环境**: worktree `D:\WorkSpace\finance_analysis_agent\.worktrees\screen-stack`
**验证范围**: Task 1–4（合同校验 + repair 循环 → 用途注册 + 双档路由 → 跨桶 prompt + 软评分 → 真跑与部署收口）
**全量回归时 HEAD**: `067c0119`（T1–T3 三个 feat 提交之上的工作区；此后仅新增本报告、真跑脚本与 tasks 回填，零 src 改动）

> **边界声明（只记录实际发生）**：本 change 的 LLM 调用为**真跑可选**组件——单测全部经 stub client
> （零网络，见 §0.1）；§1 的真跑使用宿主仓库 `.env` 的现有 provider 配置（Kimi k3-256k，endpoint
> `api.kimi.com`），不 mock 被测系统。本机 Langfuse `http://localhost:3000` 不可达（连接拒绝）：
> prompt 发布如实记录为**待部署**，trace metadata 仅经单测取证（§2）。

## 0. 命令证据（当场跑、逐字记录）

### 0.1 指定测试命令

```
$ uv run pytest tests/factors tests/llm -q
1 failed, 478 passed in 13.31s
（失败用例：tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default）

$ uv run pytest "tests/llm/test_resolver.py::TestContextLengthOverride::test_empty_follows_registry_default" -q
1 passed in 0.05s
```

唯一失败归因（如实记录）：**既有环境性 flake，非本 change 引入**——① 单独运行通过
（0.05s）；② 同一失败在上一轮全量回归（`2026-10-13-add-factor-dsl-validation.md` §0.2）已出现并
归因于宿主 `.env` 泄漏 `LLM_MAX_CONTEXT`；③ 本 change 的 src 改动仅 `factors/propose.py`（新）
与 `llm/types.py`（字面量增一项）、`llm/router.py`（REQUIRED_CAPS 增一行），不触碰 resolver/context
override 逻辑。控制台另有 `Failed to export span batch due to timeout`（Langfuse 离线导出噪音，
非用例失败）。

### 0.2 Lint 与类型检查

```
$ uv run ruff check src/finance_agent/factors src/finance_agent/llm tests/factors tests/llm \
    tests/validation/2026-10-13-add-factor-proposal-live-run.py
All checks passed!

$ uv run ruff format --check src/finance_agent/factors/propose.py src/finance_agent/llm/types.py \
    src/finance_agent/llm/router.py tests/factors/test_propose.py tests/llm/test_router.py \
    tests/validation/2026-10-13-add-factor-proposal-live-run.py
6 files already formatted

$ uv run mypy src/finance_agent/factors/propose.py src/finance_agent/llm/types.py src/finance_agent/llm/router.py
Success: no issues found in 3 source files

$ openspec validate add-factor-proposal-agent --strict
Change 'add-factor-proposal-agent' is valid
```

### 0.3 真跑（live，2 批 broad 各 ≥20 条）

```
$ uv run python tests/validation/2026-10-13-add-factor-proposal-live-run.py --dry
env 文件： D:\WorkSpace\finance_analysis_agent\.env
凭据存在性（只报存在与否，不打印值）： {'LLM_API_KEY': True, 'LLM_BASE_URL': True, 'LLM_MODEL': True, 'LLM_QUICK_MODEL': True}
endpoint host： api.kimi.com
路由决策（broad 档）： {"profile": "env:openai/k3-256k", "provider": "openai", "model": "openai/k3-256k",
                      "capability": {"tools": "single", "json_schema": "json_mode"},
                      "fallback_chain": ["env:openai/k3-256k"]}
双档 profile： {"quick": "openai/k3-256k", "deep": "openai/k3-256k"}

$ uv run python tests/validation/2026-10-13-add-factor-proposal-live-run.py
[batch-1（动量占优 70% → 跨桶引导）] 86.0s proposals=25 rejected=0 DSL=25/25 hypothesis=25/25
  category={'波动率': 6, '反转': 6, '价量': 7, '流动性': 6}
  alignment={'0.9': 2, '0.8': 11, '0.7': 11, '0.6': 1}
[batch-2（均衡采样）] 79.1s proposals=32 rejected=0 DSL=32/32 hypothesis=32/32
  category={'价量': 12, '流动性': 6, '波动率': 5, '反转': 5, '动量': 4}
  alignment={'0.8': 22, '0.9': 10}

合计：proposals=57 rejected=0 批次失败=0
明细已落：tests/validation/2026-10-13-add-factor-proposal-live-run.json
```

凭据经与 `src/finance_agent/api.py` 无参 `load_dotenv()` 同源的 `find_dotenv()` 装载（worktree 场景
解析到宿主仓库 `.env`）；**脚本与输出均不含任何密钥值**（只报 key 存在性、endpoint host、模型名）。

### 0.4 prompt 部署尝试（如实记录为待部署）

```
$ uv run python scripts/deploy_prompts.py
[ERROR] 缺少环境变量: 'LANGFUSE_PUBLIC_KEY'
发现 15 个 prompt 文件，排除 0 个
目标 Langfuse: http://localhost:3000
标签: ['production']

$ curl -s -o /dev/null -w "%{http_code}" --max-time 6 http://localhost:3000
000        （连接拒绝，exit=7）
```

双重受阻、如实记录：① worktree 无 `.env`（脚本按 `parents[1]/.env` 读取，worktree 根不存在该文件）；
② 即便补上凭据，本机 Langfuse `localhost:3000` 亦不可达。**结论：`prompts/factor_proposal.md`
为待部署状态（Langfuse production label 无该 prompt）**；按 `prompt-deploy-consistency` 口径，
eval 门禁在部署前会拒绝引用该 prompt 的运行，不伪称已部署。

## 1. 真跑核对表（57 条提案；2 批 broad 各 ≥20）

批次构造：batch-1 分桶存量 `{动量:7, 价量:2, 反转:1}`（动量占 70% → 触发跨桶指令）；
batch-2 均衡 `{价量:5, 流动性:4, 波动率:3, 反转:3, 动量:2}`。两批均要求 `n=25`（≥20）。

| 指标 | batch-1 | batch-2 | 合计 |
|---|---|---|---|
| 耗时 | 86.0s | 79.1s | — |
| 提案数（入 proposals） | 25 | 32 | 57 |
| 丢弃数（rejected） | 0 | 0 | 0 |
| **DSL 可解析率**（独立复核：对每条 proposal 的 expression 再 `parse()`） | 25/25 | 32/32 | **57/57 = 100%** |
| **hypothesis 完整率**（独立复核：`len(strip) ≥ 30`） | 25/25（47–55 字，均值 51.0） | 32/32（56–67 字，均值 62.1） | **57/57 = 100%**（最短 47 字） |
| category 分布 | 价量 7 / 波动率 6 / 反转 6 / 流动性 6 / **动量 0** | 价量 12 / 流动性 6 / 波动率 5 / 反转 5 / 动量 4 | 价量 19 / 流动性 12 / 波动率 11 / 反转 11 / 动量 4 |
| alignment 分布（脚本 0.1 桶） | 0.9:2 / 0.8:11 / 0.7:11 / 0.6:1 | 0.8:22 / 0.9:10 | — |
| alignment 精确统计（JSON 逐条值） | min 0.62 / 中位 0.75 / max 0.90；缺失 0 | min 0.79 / 中位 0.84 / max 0.90；缺失 0 | min 0.62 / 中位 0.82 / max 0.90；方差 0.004；缺失 0 |
| 批内表达式去重 | 25/25 互异 | 32/32 互异 | 跨批重复 2 条（见 §2） |
| trace metadata | `purpose_tag=factor_proposal`、`mode=broad`、`router=...`、`rejected=[]` | 同 | 无 `proposal_rejected`（本轮零丢弃） |

**人工核对材料（抽 10 条；全 57 条逐条见 `2026-10-13-add-factor-proposal-live-run.json`）**：

| expression | category | alignment（自评） | hypothesis |
|---|---|---|---|
| `ts_std(div(sub(high, low), close), 10)` | 波动率 | 0.9 | 日内高低价差相对收盘价衡量单位价格承载的分歧程度，分歧持续放大后往往预示波动率状态切换与风险定价重估。 |
| `sub(ts_mean(close, 10), close)` | 反转 | 0.88 | 当前收盘价低于近端均价时，短线持仓浮亏压力上升，悲观情绪过度释放后存在向均衡价格修复的反转动力。 |
| `rank(ts_corr(close, volume, 10))` | 价量 | 0.77 | 价格与成交在一段时间内的协同度经截面排序后，可识别放量上涨或缩量下跌的差异，量价背离常隐含趋势质量下降。 |
| `rank(ts_mean(amount, 20))` | 流动性 | 0.83 | 近月成交金额的截面排名代表可交易容量和市场关注度，容量差异会影响冲击成本、机构参与度以及风险溢价补偿。 |
| `ts_rank(max(high, close), 10)` | 价量 | 0.62 | 日内最高价与收盘价的较高者进入近端分位，可观察上沿压力是否被反复测试，强势收盘贴近高点时流动性买方更主动。 |
| `log(div(close, ts_mean(close, 20)))` | 动量 | 0.84 | 现价与二十日均线之比取对数得到近似二十日对数收益，平滑的单期动量刻画中期趋势强度，趋势惯性源于信息扩散缓慢与投资者锚定不足。 |
| `div(ts_std(close, 10), ts_mean(close, 10))` | 波动率 | 0.85 | 价格标准差除以均值的变异系数消除股价量纲影响，实现波动率横截面可比；低波动异象表明高风险股票收益风险比恶化，低波动股票获得超额收益。 |
| `sub(ts_mean(close, 20), close)` | 反转 | 0.88 | 二十日均线减去现价衡量股价偏离长期均衡的程度，价格大幅跌破均线反映短期超卖，基于均值回复力量，超跌个股存在向上修复的超额收益。 |
| `mul(zscore(delta(close, 5)), zscore(ts_std(volume, 10)))` | 价量 | 0.82 | 当价格变动与成交量波动同步异常放大时，说明短期博弈情绪极端，后续存在情绪均值回归带来的反向收益，二者乘积可捕捉量价共振的极端状态。 |
| `sub(ts_corr(high, volume, 10), ts_corr(low, volume, 10))` | 价量 | 0.79 | 最高价与最低价分别对成交量的相关之差刻画资金在价格上下沿的行为差异，高位放量相关性更强说明追涨盘主导，隐含情绪过热后的回撤风险。 |

抽样目检结论（自动化 runner 的目检，非人工终审）：10 条全部满足「表达式语义 ↔ 假设语义」一致
（未出现「动量因子配反转假设」式货不对板）；expression 仅使用 DSL 算子/字段，无发明算子；
hypothesis 均为可检验的经济学表述而非同义反复。

## 2. 异常记录与口径注记

| 项 | 状态 | 说明 |
|---|---|---|
| 真跑未触发 repair 路径（主动披露） | 已记录 | 57 条全部首轮通过合同校验（rejected=0、无修复轮），因此 **repair 与 `proposal_rejected` trace 标记未被真跑覆盖**；其行为由单测钉死（`tests/factors/test_propose.py`：`[坏,坏,好]` 第三次成功、`[坏,坏,坏]` 丢弃且 trace metadata 记 `proposal_rejected` 与逐条 raw+error、部分收编、非 JSON 响应计入修复）。真跑覆盖的是「正常路径」，不构成对 repair 的实证 |
| Langfuse 离线 → trace metadata 未实证（主动披露） | 已记录 | `localhost:3000` 连接拒绝（§0.4）；`purpose_tag/mode/router/rejected` 的落点仅为**单测断言**（`tests/factors/test_propose.py::TestGatewayClient`、`TestTierRouting`）+ 真跑脚本落盘 JSON（同一 dict 内容）。Langfuse UI 侧不可取证 |
| alignment 为 LLM 自评（口径注记） | 已钉住 | 分布 0.62–0.90（中位 0.82、方差 0.004），呈自评常见的「宽松 + 居中」形态（batch-2 全部落在 0.79–0.90）；**该分值不参与任何硬拒收**（P5 单测：0 分照常进入 proposals、越界收敛、非数值按未评分处理）；无人工标注校准前不得作为质量判据使用 |
| 跨批重复表达式 2 条（主动披露） | 已记录 | `div(sub(close, low), sub(high, low))` 与 `ts_corr(rank(close), rank(volume), 10)` 在两批各出现一次；本 change **不做去重**（去重是因子库五道闸职责，属 `add-factor-library-and-monitor` 非目标；`factors/dsl.py::is_subtree_isomorphic` 已备好原语） |
| 跨桶指令的引导强度（观察） | 已记录 | batch-1 动量桶占 70% 时为 0/25 动量提案（其余四桶 6/6/7/6）——显式指令生效且强度较高；是否「过度导向」属后续调参问题（spec 只要求「显式要求候选偏向其他桶」，已满足） |
| 响应条数可多于 n（口径注记） | 已钉住 | batch-2 请求 n=25、实得 32 条。`n` 为 prompt 级要求（入参硬校验 n ≥ 20），**合同只逐条约束提案，不因响应条数少于/多于 n 拒收整批**（docstring 明示） |
| 双档当前解析到同一模型（环境事实） | 已记录 | 宿主 `.env` 的 `LLM_MODEL` 与 `LLM_QUICK_MODEL` 均解析为 `openai/k3-256k`（§0.3），故真跑未体现 quick/deep 的**模型差异**；档位决策差异由单测钉死（monkeypatch 双 profile：broad→quick、deep→deep、quick 缺 json 能力时切换到 deep、全不满足抛 `UnsupportedCapabilityError`） |
| route 审计 trace 与调用 profile 钉死（实现注记） | 已披露 | `GatewayProposalClient` 把路由选中的 profile 钉成 `preset=`（命名 preset）或请求级 `llm_config`（env 端点 + provider_options 原样）后才调 `complete_text`；registry 官方端点语义（无 base_url）回落 gateway 按 purpose 解析（同一 profile）。「经 PolicyRouter 解析」是真实生效路径而非装饰 |
| 探测缓存 / 环境依赖（测试卫生） | 已核实 | `tests/conftest.py` 的 `_reset_probe_cache` autouse fixture 逐测试重置 probe 缓存；本 change 单测零网络（stub client + `loader._get_client → None` autouse fixture） |
| 既有测试零修改（红线 4.3） | 见 §0.1 | 仅 `tests/llm/test_router.py` **追加** 3 例（既有断言零改动，符合计划「只在既有 router 测试文件追加用例」）；其余全部新增文件 |
| live 依赖 | 见 §0.3 | 真跑依赖宿主 `.env` 的 LLM_* 凭据（已具备）；Langfuse 离线仅影响 prompt 托管与 trace 观测，不阻塞业务路径 |

## 3. 结论

**通过项（本报告取证）**：

- [x] `uv run pytest tests/factors tests/llm -q` → 478 passed（含新增 35 例 propose + 3 例 router）；
      1 例既有环境 flake（隔离复跑通过，归因见 §0.1）
- [x] `uv run ruff check`（新增/涉及文件）→ All checks passed；`ruff format --check` → 6 files already formatted
- [x] `uv run mypy`（3 个 src 文件）→ Success: no issues found
- [x] 真跑 2 批 broad：57 条提案，DSL 可解析率 57/57、hypothesis 完整率 57/57、rejected 0（§1）
- [x] 跨桶防收敛实证：动量占 70% 的 batch-1 产出 0 条动量提案、其余四桶均衡覆盖（§1）
- [x] 既有测试零修改（仅追加；§2 末二行）
- [x] 双档路由 / 能力过滤 / typed error / 软评分不拒收 / repair 上限 → 单测钉死（§0.1、§2）

**未执行项 / 移交**：

- [ ] **prompt 部署（待部署）**：`factor_proposal.md` 未发布到 Langfuse production label（双重受阻见 §0.4）——部署前不得运行引用该 prompt 的 eval 门禁
- [ ] **50 条提案的人工终审签字**：机器核对（可解析率/假设完整率/分布）已 57/57 完成；语义一致性的**人工**终审待人签字（材料：本报告 §1 抽样表 + JSON 全量逐条）
- [ ] repair 路径与 `proposal_rejected` trace 标记的真跑实证（本轮 0 丢弃未覆盖；属 LLM 行为事件，需构造对抗输入或等待自然出现）
- [ ] 跨批结构去重（非目标，移交 `add-factor-library-and-monitor`）
- [ ] alignment 自评分值的人工标注校准（进入指标前必须过 AGENTS.md 的「分桶归因 + 逐条终裁」纪律）
