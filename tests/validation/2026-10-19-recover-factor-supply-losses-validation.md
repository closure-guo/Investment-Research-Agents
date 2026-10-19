# 验证报告: recover-factor-supply-losses

**日期**: 2026-10-19
**验证人**: agent（复跑取证；裁决与读数均可复现）
**关联 delta**: `openspec/changes/recover-factor-supply-losses/`
**E2E 门禁**: 不适用（纯后端/供给线变更，无前端 UI / SSE / 会话状态交互面）
**复跑脚本**: `tests/validation/2026-10-19-recover-factor-supply-losses-replay.py`（R1/R2 零 LLM）
＋ `scripts/replay_factor_harvest.py`（R4 实网，临时 state/库）
**产物**: `reports/recover-factor-supply-losses-replay.json`、`reports/replay_r4.json`（本地不入库）

## 结论

| 项 | 读数 | 判定 |
|---|---|---|
| T1 负数常量重排 | `-1 * $close` → `sub(0,close)` 等 8 形态 + 数值等价（含 NaN）用例全绿；负窗口仍拒收 | ✅ |
| T2 深度上限 6→8 | 深度恰 8 通过 / 9 拒收；供给线深度 7 形态（Amihud 族）通过 | ✅ |
| T3 转译合同失败重试 | 单测红→绿（attempts=2 成功 / attempts=3 留痕 / 网关异常调用计数=1）；**R4 实网 attempts=2 实证** | ✅ |
| T4 按状态重放 | 单测红→绿（覆盖 + `retried_from` / 重放优先 / 缺省幂等不变）；ops 脚本 dry-run 与迁移对照 | ✅ |
| 代码门禁 | `pytest tests/factors tests/scripts/... tests/test_api_*` = **423 passed**；ruff 干净；mypy 4 文件无问题 | ✅ |
| prompt 同步 | `factor_proposal.md`「≤ 8」已发布（Langfuse production **v4**，含「≤ 8」实证） | ✅ |

**核心读数（复跑，临时库）**：本 change 使 **6/19 条 translator 阶段不可翻译候选恢复可翻译**
（负数常量 3 + 深度 3），并让 2 条转译失败候选经重试拿到正式裁决；**恢复的 6 条全部倒在闸②
（|ICIR| 0.069–0.273，门槛 0.3）→ 0 入库**——翻译修复回收的是「供给利用率」，不改变
「候选池强度是主要矛盾」的既有结论（闸②门槛复审备忘，2026-10-17）。

## R2：translator 阶段不可翻译复译（19 条 → 6 恢复）

恢复（6）：`gap_rev1` / `rev1` / `rev5`（负数常量重排）、`illiq20` / `illiq_20d` / `klow_5d`（深度 7–8）。
**review 修复后复测**（`reports/recover-factor-supply-losses-replay-after-fix.json`）：恢复集不变；
调用形态修复（见 §review 修复 R2）使 `parkinson20` / `downvol20` 的自由参数各降 1（5→4 / 14→13），
仍倒在参数轴——该族需要的是自由参数上限决策，不是重排覆盖。

过闸读数（新面板 n≈229–249，闸②门槛 0.3）：

| 候选 | 裁决 | |ICIR| |
|---|---|---|---|
| gap_rev1 | 闸② WEAK_ICIR | 0.207 |
| rev1 | 闸② WEAK_ICIR | 0.087 |
| rev5 | 闸② WEAK_ICIR | 0.084 |
| illiq20 / illiq_20d | 闸② WEAK_ICIR | 0.069 |
| klow_5d | 闸② WEAK_ICIR | 0.273 |

仍拒（13）：自由参数轴 12 条（含 `lowvol20`/`parkinson20` 等 3 条负数常量族——**重排的
`sub(0,x)` 形态多计 1 个自由参数（0），恰好越过上限 3**，见 §剩余决策项）、研究侧公式残缺
（`ret_autocorr_5d` 缺右括号）1 条。

## R1：已拒候选复检（38 条 → 0 变化）

38/38 裁决全部不变（闸②×31、闸④×6、闸①×1；`admitted=0, errors=0`）。
关键样本：`vol_ratio_20d` 现越过闸①（面板 150→250 生效）但**仍倒在闸④样本外增量**
（increment = −0.365）；`mom20` |ICIR| = 0.187（150 日面板读数 0.153）；闸②族
|ICIR| 中位 0.187 / 最大 0.287。**结论：面板加深与代码演进未翻转任何既有拒收裁决，
「已考全拒」不是测量伪影。**

## R4：transcription_error 实网重试（2 条 → 均获正式裁决）

| 候选 | 原状态 | 重试后 | attempts | 后续边界 |
|---|---|---|---|---|
| skew20 | transcription_error | untranslatable（LLM） | 1 | 缺滚动偏度算子（Skew）——需算子扩展决策 |
| ret_skew_20d | transcription_error | untranslatable（translator） | **2** | 复杂度超限（深度 11 / 自由参数 37）——矩法偏度公式天然超限 |

**重试机制实网实证**（`ret_skew_20d` 第 2 次尝试拿到合法响应）；两条的残余边界均从
「工程性失败」归位为「正式的可判读裁决」。

## 异常记录

- R1/R2 各自独立空临时库（`tmp/replay_r1.db` / `tmp/replay_r2.db`），忠实于「重放当刻生产
  因子库为空」；R1 以单批 38 条执行（闸③ BH-FDR 批内分辨率 K=38 ≤ 100，批组成与历史
  两批 11+27 不同——读数对批敏感，此为复检口径声明）。
- R4 以 `--limit 5` 执行（重放 2 条先占批，另 3 条未处理候选一并入批）——仅写临时 state，
  未触碰生产状态与生产因子库；生产 `harvest_state.json` 与 `factor_library.db` 全程只读。
- 复跑全程未重启容器、未改主检出（改动全部在 `.worktrees/factor-supply-losses`）。

## review 修复记录（双审查者：规格合规 + 代码质量，general-purpose 派发）

| # | 分级 | 发现 | 处置 |
|---|---|---|---|
| R1 | **Critical** | 重试面泛捕获 `ValueError`，把 LLM **配置类**错误（resolver `IncompleteLLMConfigError`/`UnknownProviderPrefixError`）当合同失败重试并落 `transcription_error` 状态——违反 fail-loud 与「未处理候选不记状态」 | 收窄为专用异常：载荷合同失败在 `transcribe_candidate` 边界归一为 `TranscriptionResponseError`；新增 `IncompleteLLMConfigError` 用例（不重试、不写状态，调用计数=1）；耗尽改抛 `_ContractExhaustedError`（携带**实际**尝试次数） |
| R2 | **Important** | 重排表只盖中缀 `_BinOp`；`Mul(-1, X)` 调用形态绕过重排、多计自由参数（生产 `parkinson20` 即该形态） | 2 元 Add/Sub/Mul/Div 调用形态与中缀**共用**同一重排表（`_QLIB_TO_BINOP` 归一）；新增调用形态矩阵 + 数值等价扩展用例；design.md 的 Div k=1 条目勘误（`sub(0,x)` 传播 ±inf 而引擎 `div` 掩非有限为 NaN，故不做该特例） |
| R3 | Important | 本 change 核心行为测试（翻译器契约 / 重放脚本）落在 `tests/scripts/`——**不在默认套件与 CI 门禁内** | `git mv` 至 `tests/factors/test_qlib_translate.py` 与 `tests/test_replay_factor_harvest.py`（CI 收集面），模块 docstring 记录迁移与理由 |
| R4 | Important | ops 脚本误用护栏不足：`--statuses admitted` 会把库内 active 的审计状态改写成「已拒」；坏状态文件被静默读成 `{}` 后整体改写；无目标路径回显 | 状态白名单（仅 untranslatable/transcription_error/rejected）；状态读取 fail loud（退 2）；`--limit` 限 1-100；执行前回显 candidates/state/factor_db/snapshot 四个目标路径 |
| R5 | Minor | 交付物勾选与报告矛盾；delta 缺 `(Previously:)`；弱断言（OR 族）；`attempts` 硬编码；重放覆盖丢 `qlib_expression`；文档措辞过强 | 逐条修复：tasks 回填；补 `(Previously:)` 两条；裁决断言钉死（闸② WEAK_ICIR）；attempts 实测；重放保留历次 `qlib_expression`；等价口径改为「数值一致（符号零位级差异，不可观测）」；`_abs_num`/`_recorded_status` 防御半截状态文件 |

## 剩余决策项（不在本 change，本报告提供依据）

1. **sign / 滚动 Min-Max 算子**（解锁 LLM 拒译族 9 条：sign 5 + Min/Max 3 + 其它 1）——
   需 DSL 扩展 + `factor_transcription.md` 算子清单同步 + Langfuse 发布；另 `skew20` 类需
   Skew 算子。预计收益：候选进考场，但按本报告读数，进考场 ≠ 入库。
2. **自由参数上限 3 是否放宽**：本 change 实测其拦截面（R2 仍拒 12 条均为该轴）；
   放宽属治理决策（过拟合轴），建议维持（闸②复审备忘立场）。
3. **负数常量重排的零参数编码**（`sub(x, add(x,x))` ≡ `−x`，0 自由参数）可回收
   `lowvol20`/`parkinson20` 等，但可读性与结构查重可审计性差——当前采用 `sub(0,x)`，
   零参数编码留档为候选方案。
4. **研究侧产物质量**：ε 未数值化 7 条 / 公式残缺 1 条——治研究侧（RD-Agent prompt 约束）。

## 结论

- [x] 全部通过（代码门禁 423 passed / ruff / mypy / 复跑读数齐备），可进入 sync + archive
- [ ] 存在失败项，需修复后重新验证
