# Design: add-price-polarity-contract

## Approach

契约 owner 放在 validate 与 ingest 之间（「总翻译」位置）：`TradeDecision.sell_type`（models.py:211 已有）→ 派生 polarity 声明 → validate 校验按声明极性 → ingest 冻结带声明。不新增独立模块文件也可以——先落成 validate/ingest 内共享的单一派生函数（`_polarity_of(decision)`），若后续执行链也需要消费再提取。**刻意不做** 4 份 action→direction 映射的运行时统一（那是更大的重构，见 architecture-debt c2 档案）；本 delta 只收「字段层语义无契约」这个最小闭环。

## Alternatives Considered

- 方案 A：把 direction 映射整个统一（4 份拷贝归一）——推翻决策域/执行域/结算域各自的既有语义，波及 #595/#597 刚落的执行消费逻辑，收益/风险比差，放弃。记分卡反向（#606 影响 3）属统计语义，另行拍板。
- 方案 B：只修 validate 的校验分支、不动落库声明——治标不治本：字段仍无契约，消费方还得猜，且 FM 更正回写没有落点。

## Risks

- 存量 30 条混用记录：polarity=NULL，展示标「口径未声明」，不追溯——与 settle_entry_price 存量 NULL 不回填先例一致。
- validate 行为变化可能影响价位校验阻断率：上线后按 incident 026 纪律分桶归因，混淆「契约修复带来的合法通过」与「校验器缺口误报」。
- 前端改动触发交互类管线（E2E + 人工验证）：E2E 用 stub 套件，口径标注是确定性 UI 行为，风险低。

## Open Questions（待拍板，不在本 delta）

- `sell=exit` 观点的 direction 映射与记分卡结算语义：exit 叙事成功（反弹卖出执行好）与 `direction=short` 结算得分系统性反向——是否引入 exit 专用判定语义（如 direction=exit + 回避式判定），另行 delta。
- settle.py `evaluate_decision` 死代码删除（#606 影响 2 的陷阱根除）——已在 C6 小件② 立项，本 delta 不动它。
