# 人工验证报告: add-price-polarity-contract

**日期**: 2026-11-21
**验证人**: agent（程序证据部分）+ 维护者（人工抽查项待确认）
**关联 delta**: openspec/changes/add-price-polarity-contract/
**E2E 门禁**: tests/e2e/playwright/tests/track-record-polarity.spec.ts 本地 2 passed；track-record 全套件 22 passed；CI stub pass（PR #612 run 44887099317）

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| exit 决策按 exit 惯例校验（600029 B 型） | 是（单测极性矩阵 + spec） | stop<entry<target 判 pass，不镜像 | 单测过；E2E exit 态标注「清仓位·跌破清出/反弹减仓带」 | ✅ |
| short 决策按 short 惯例校验 | 是（单测 + E2E short 态） | stop>entry>target 判 pass | 单测过 | ✅ |
| 分型与极性错置被拦截（600771 A 型） | 是（单测） | exit 分型 + short 形状 → fail 打回 | 单测过 | ✅ |
| FM 记账口径更正回写 | 是（单测 4 例） | approve + price_correction → 终稿重写 + 修正来源标注 | 单测过 | ✅ |
| 极性声明随快照冻结 | 是（单测 + seed 集成测试） | declared_prices.polarity 落库 | 集成测试过 | ✅ |
| 消费方按声明极性解释 | 是（E2E 两态） | 详情页语义标注 | E2E 2 passed | ✅ |
| 存量混用不追溯但可辨识 | 是（E2E 存量态） | declared_polarity=None + 「口径未声明」 | E2E 过；生产探针见下 | ✅ |
| 部署后生产读数 | 否（生产探针） | /api/health ok；行带 declared_polarity | health 全 ok；存量 50 行（11-19 22:00 后）declared_polarity 全 None，其中 sell 35 条 | ✅ |
| **人工抽查：存量 30 条展示辨识** | 否 | 详情页 stop/target 标「口径未声明」，不虚构惯例 | **待维护者抽查**（候选见下） | ⬜ |
| **人工抽查：exit 决策价位不被镜像改写** | 否 | 新产出的 sell=exit 决策价位与叙事一致 | **待下一批实盘分析抽查** | ⬜ |

## 人工抽查候选（生产库，11-19 22:00 后 sell 存量行）

访问 `http://localhost:5173/track-record/predictions/{prediction_id}`：

| prediction_id 前缀 | 标的 | entry / target | 期望展示 |
|---|---|---|---|
| p_9a35d0 | 广誉远 600771 | 11.51 / 12.0629 | 「口径未声明」（A 型错置样本） |
| p_5f3417 | 韵达股份 002120 | 6.05 / 5.83 | 「口径未声明」 |
| p_08e5bc | 南京港 002040 | 7.24 / 6.9286 | 「口径未声明」 |
| p_0fba6b | 诺瓦星云 301589 | 148.0 / 134.63 | 「口径未声明」 |
| p_c70e43 | 海南机场 600515 | 2.47 / 2.38 | 「口径未声明」 |

## 异常记录

（无——程序验证全部通过；CI 首跑红一次，根因为 evals/ 顶层懒导入漏扫，已修 cc846758 后 CI 双绿）

## 结论

- [ ] 全部通过，可 archive（待维护者完成人工抽查两 ⬜ 项后勾选）
- [ ] 存在失败项，需修复后重新验证
