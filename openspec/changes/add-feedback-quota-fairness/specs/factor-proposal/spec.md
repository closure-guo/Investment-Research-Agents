# Delta for factor-proposal

## ADDED Requirements

### Requirement: 回喂清单配额公平性

跨会话记忆桥导出载荷（已探索清单）SHALL 在分档分配上保证类别级最低代表性：rejected 档内**每个出现过拒收的闸口 SHALL 获得保底出场配额**（默认每闸 ≥5 条，可配置；该闸累计拒收不足保底数时全量出场），保底之外的余量 SHALL 按各闸累计拒收量占比分配；单一闸口 MUST NOT 独占 rejected 档全部出场位。untranslatable 档 SHALL 在既有按日轮转语义之上保证**失败类别（stage）最低代表性**——单一 stage MUST NOT 独占该档配额。保底未用满的空位 SHALL 归还其他类别使用（不得留空位浪费总量）。既有语义 SHALL 零回退：admitted 全量在前、按规范化表达式去重、总量 `limit` 上界、`contract` 块与 `gate_rejections` 聚合照常存在。载荷 SHALL 附配额分配读数（每档与每类别的实际出场条数），供监控页与判效核对。

#### Scenario: 闸②垄断被打破

- GIVEN 累计 rejected 含闸② 395 条与闸④ 84 条（其余闸口拒收量可忽略），`limit=80`、untranslatable 保底配额 20、admitted 12 条
- WHEN 执行记忆桥导出
- THEN rejected 档出场中闸④ ≥5 条（保底命中）
- AND 闸② 出场数少于 rejected 档全部出场位（垄断解除）
- AND 载荷总条数 ≤ `limit`

#### Scenario: 保底不足额全量出场且空位归还

- GIVEN 某闸口累计拒收仅 2 条（< 保底 5）
- WHEN 执行记忆桥导出
- THEN 该闸 2 条全部出场
- AND 未用满的 3 个空位由其余闸口按占比使用，`returned` 总条数不因保底机制下降

#### Scenario: untranslatable 失败类别代表性

- GIVEN untranslatable 档配额 20，translator 段 300 条、LLM 段 60 条
- WHEN 执行记忆桥导出
- THEN 两个 stage 均有出场（各自达到最低代表性）
- AND 档内按日轮转语义保持（早期批次仍可见）

#### Scenario: 配额分配读数随载荷落盘

- GIVEN 任一次成功导出
- WHEN 载荷写出
- THEN 载荷含配额分配读数字段（每档与每类别实际出场条数）
- AND 读数与 `explored` 清单实际构成一致（可程序核对）

#### Scenario: 既有语义零回退

- GIVEN 与上一版相同的收割状态与候选文件（含 admitted、untranslatable、rejected、transcription_error 四档与契约块）
- WHEN 执行记忆桥导出
- THEN admitted 仍全量且位于清单首位、按规范化表达式去重、总量 ≤ `limit`
- AND `contract` 块与 `gate_rejections` 聚合字段照常存在（feed-dsl-contract-to-factor-generator 语义不受影响）
