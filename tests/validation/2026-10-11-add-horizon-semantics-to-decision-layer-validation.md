# 人工验证报告: add-horizon-semantics-to-decision-layer

**日期**: 2026-10-11
**验证人**: ZCode（会话代执行，owner 终审）
**关联 delta**: openspec/changes/add-horizon-semantics-to-decision-layer/（#321 已合入 6c1ecc78）
**E2E 门禁**: 不适用（纯后端 prompt + Claim 模型变更，非交互类）；CI lint-and-test + stub 套件全绿
**修复随档**: #323（aececbe0）——E2E 实跑暴露的价位校验器指数名误拦

## 验证环境说明

验证在隔离 worktree 后端完成（端口 8010，`SESSIONS_DB_PATH=data/sessions_e2e_horizon.db` 隔离库，真 LLM、真数据管道），**未重启生产容器**：主检出有另一会话在途未提交改动（`report.py` 等），pull 会撞在途工作；生产部署（主检出 pull + 容器 restart）留待正式窗口。prompt 已从 worktree 正式发布 Langfuse production（7 导入 / 7 跳过 / 0 失败）。

## 实跑记录（600519 贵州茅台，深度分析，共三跑）

| 跑次 | 模型 | 结果 | 说明 |
|---|---|---|---|
| 1 (22:44) | kimi k3-256k | 阻断 | 价位校验器误拦「沪深300」→ 抓到真 bug → #323 |
| 2 (23:03) | kimi k3-256k | 失败 | **kimi 周配额（7-day window）耗尽**——供应商限额家族（10-09 glm 5h 熔断、ark 订阅过期之后）；**下周一生产日批将同样撞墙，需 owner 处理** |
| 3 (23:07) | glm-5.3-flash（请求级 llm_config 路由 judge 通道） | **completed**（36.4 分钟） | 全链路交付，验证以下各项 |

## 验证结果

| 验收项 | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|
| 周期契约注入三决策层 | trader/risk_judge/fund_manager 含同源契约段 | 契约测试断言同源逐字一致（48/48）；实跑 reasoning 行为化（见下） | ✅ |
| buy/watch 论据周期对齐 | 决策提考核窗与兑现路径 | 终稿 reasoning 原文：「**T+20 窗口届满无论是否触发均强制重评，杜绝无期限观望**」——契约语义被 LLM 行为化为决策机制（附带正面命中 10-10「全观望锁定」滞回病灶） | ✅ |
| FM 审批周期一致性 | approve 定性与终稿一致、核查含周期 | FM 审批通过（watch，0.65），reasoning 核验「季报披露日或 **T+20 届满孰早强制重评**」+「决策价位校验——打回后已修正」（价位门禁全程工作） | ✅ |
| claims horizon_hint 申报 | 分析师 claim 携带兑现窗口元数据 | **申报率 98.2%（110/112）**：fundamental 66/66、macro 15/15、sentiment 10/10、technical 19/21；2 条 null=「无法判断置 null」设计如实生效（非编造档位） | ✅ |
| 档位语义正确性 | short/mid/long 与论据性质匹配 | short 13 / mid 89 / long 8；抽样核对：段永平增持事件→short、提价长线利润增厚→long、政策呵护→mid——语义正确 | ✅ |
| 报告正文周期语义 | 报告提考核口径与兑现 | 正文命中 T+20/沪深300/兑现表述（14.5k 字符完整交付） | ✅ |
| 价位门禁不被指数名误拦 | 沪深300 不再被当股价 | 跑 3 正常交付（跑 1 同 token 被拦）；`test_decision_price_check.py` 48/48 含 #254 家族全部回归护栏 | ✅ |
| Claim 模型向后兼容 | 存量 claim 不带新字段正常解析 | 单测覆盖（保留值/缺失/非法值拒绝）+ 实跑 112 条 claims 全量解析无异常 | ✅ |

**citations verdict 分布**：verified 93 / unchecked 11 / failed 10——failed 桶为值级偏差的既有语义（定向重试后仍偏差的记录，不阻断报告），非本 delta 引入，不展开。

## 异常记录与归因

1. **跑 1 价位校验器误拦（本 delta E2E 抓到的真 bug，已修）**：契约段注入后 reasoning 恒含「相对沪深300 超额收益」，校验器把 300 提为文本价位与近期低点 1170.28 交叉校验（偏差 74.37% 精确吻合 `|300−1170.28|/1170.28`），打回重试也无法去掉（prompt 要求写考核基准）→ 报告阻断。既有守卫皆不设防（±10 倍量纲窗 3.9x、关键词窗无指数前缀），属 #254 误拦家族新变种。修复：`_exclusion_spans` 增补指数名整体排除（沪深/中证/上证/深证/北证/国证/恒生/纳斯达克/标普/道琼斯 + 直接跟随 2-4 位数字），护栏测试钉死名称后隔开的真实价位仍受校验。TDD 先红后绿，PR #323 已合并（aececbe0）。
2. **跑 2 kimi 周配额耗尽（外部，与本 delta 无关）**：`UnknownLLMError: You've reached your weekly (7-day) usage limit`。**影响生产：下周一（10-12）日批分析链路将同样失败**，与 10-09 glm 5h 熔断、ark 订阅过期同族，需 owner 决策（续费/切换）。
3. **predictions 表未落库（隔离环境现象，非本 delta 验收项）**：跑 3 完成后隔离库 predictions/decision_log 为空。落库走 `persist_prediction_from_accumulated` 共享入口，代码路径无 env 门槛、后端日志无 ERROR（旁路静默条件未定位）；本 delta 改动（prompt 字段 + Claim 可选字段 + 校验器排除）与该链路无交集，4593 项测试无相关红。归因留给生产判定日批（10-12 16:00）交叉验证，若生产同样不落库则按 track-record 家族另立案。
4. **langfuse_trace_id 为 None**：隔离 worktree 后端的 trace 上报不完整（span 生命周期警告「Calling end() on an ended span」多条），语义验证全部改从会话产物/隔离库取证。非本 delta 范围。
5. **CI stub timeline 组一红**（#323 首轮 CI）：`RuntimeError: STUB LLM 模拟失败` + ConnectionReset——已知 CI 专属时序 flake（#312 立案家族），重跑即绿。

## 结论

- [x] 全部通过，可 archive（task 7 prompt 发布 / task 8 实跑验证 / task 9 validate 均已闭环）
- 生产部署（主检出 pull + 后端容器 restart）与 kimi 周配额处置待 owner 窗口
