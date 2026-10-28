# Design: add-pool-stock-name-display

## Approach

名字在链路上只在**一处**丢失：`selection_pool` 落库。上游快照（`market_snapshot.name`）→
L1（`CandidateRecord.name`，`build_candidate_record` 已透传）全带着名字，L2 的
`Candidate`（ranker 契约 dataclass）没有 name 字段但池行组装在 pipeline 内、
L1 记录就地可得。因此改动收敛为三段：

1. **store（迁移 + 读写列集合）**
   - `SCREENING_DDL` 的 `selection_pool` 加 `stock_name TEXT`（新建库直接含列）；
   - 存量库经幂等 `ALTER TABLE selection_pool ADD COLUMN stock_name TEXT` 补齐——新增
     `_POOL_COLUMN_MIGRATIONS`，模式与既有 `_BATCH_COLUMN_MIGRATIONS` 同款（键=列名，
     值=ADD COLUMN DDL，「新增列只在此声明，迁移单点」）；
   - init 顺序固定「恢复中断 → 列补齐 → 主键重建 → 列兜底」：主键重建语句序列
     （`_POOL_TABLE_REBUILD_SQL`）**必须同步含新列**（其列集合注释承诺与 SCREENING_DDL
     逐字一致），且列兜底放在重建之后兜住「重建路径产出旧列集合表」的组合；
   - `_pool_row_payload` 增加 `stock_name` 校验：必须 `str`（None → `""`），**允许空串**
     （见缺名语义）；payload 元组列序与表列一致；
   - `record_screening_batch` 的 INSERT 列集合与 upsert 更新列集合各加一项；
     `list_selection_pool` 的 SELECT 加列（返回 dict 自动带键，API 透传零改动）。
2. **pipeline（透传）**
   - L1 阶段就地构建 `names_by_symbol = {r.code: r.name for r in records}`
     （`build_candidate_record` 已 `str(...).strip()` 规范化）；池行 dict 加
     `"stock_name": names_by_symbol.get(candidate.symbol, "")`；
   - keep 标的缺名 → `_logger.warning`（逐标的一次，含 symbol 与 trade_date），不抛。
3. **frontend（展示）**
   - 池行类型加 `stock_name?: string | null`；表格在「标的」与综合分之间插
     「股票名称」列，渲染 `row.stock_name || '—'`；组件测试与 e2e 补断言。

主档与小票通道共享全部读写函数（`pool` 参数只进 WHERE），加列自动同享，零额外接线。

## 缺名语义：展示字段不 fail loud

`_pool_row_payload` 既有纪律是「字段 fail loud，坏值不入库」。`stock_name` 有意**偏离**
该纪律：它不参与资格判定与排序（决策字段），为它让整批精选池落空（批次 failed → 当日
零池 → 页面 stale）不成比例。快照 name 实际恒有值（AKShare 全市场快照自带），缺名是
数据质量降级信号 → 空串落库 + warning 披露，前端退化为仅代码展示（= 迁移前现状）。
spec 的 ADDED requirement 已把这条语义写明，与决策字段的 fail loud 形成对照。

## Alternatives Considered

- **读侧 join 快照取名**（不动库）：`/api/screening/pool` 读池时跨库（screening.db ×
  market_snapshot.db）按 symbol join 当日快照。否决：①跨库读引入部署耦合（两库路径都
  必须可达）；②池行是历史日期，快照按日覆盖/清理后 join 会失效或改名漂移（池行的
  「当时叫什么」与「现在叫什么」不是同一口径）；③fallback 日读快照尚可，但 L2 名单
  无展示位，join 收益面窄。
- **L2 全量名单（l2_candidates）同步加名**：用户可见面是精选池，全量榜无页面展示；
  `rewrite_day_l2_board` 的写入契约（全量落库、原子替换）会为纯展示字段扩大回归面。
  否决，留待真有全量榜页面时再议。
- **会话 display_name 带名**（`pool_analysis.py` 的 `stock_name=""` 另一处口径）：
  属会话命名问题，与池表格展示是两个面；且 display_name 拼接规则有自己的既有格式，
  合并进本 delta 会让归档范围失焦。另提案。

## Risks

- **存量库迁移破坏历史池行**：ADD COLUMN 对 SQLite 是常量时间操作不动行；重建序列
  只在主键缺 `pool` 时触发（已迁移库不触发），触发时 INSERT SELECT 显式列集合（新列
  不在旧表 → migrate 表该列落 NULL，历史行保留）。对策：store 测试补「旧 schema 库
  打开 → 列补齐 → 历史行 `stock_name` 为 NULL → 读取返回 null」用例。
- **重建语句序列与新 DDL 漂移**：`_POOL_TABLE_REBUILD_SQL` 的列集合注释承诺与
  SCREENING_DDL 逐字一致，本 delta 两处都要改，漏一处会出现「新建库有列、重建后丢列」。
  对策：列兜底 ALTER 放重建之后 + store 测试「含旧 schema 数据的库走完整 init →
  新列存在且写入读取往返成功」。
- **e2e stub 种子池行无名字段**：stub seed 直接插池行的话，新列缺失 → 接口返回
  null → 页面渲染 `—`，e2e 名称断言会红。对策：seed 同步补 `stock_name`，名称断言
  用 seed 中真实写入的名称。

## Compatibility

- 存量历史池行：`stock_name` NULL → API `null` → 页面 `—`（现状等价，非回归）。
- 当日池：无需回填，同日补跑 upsert 自动补名；次批（下一交易日 15:40）起自动带名。
- API 契约：`/api/screening/pool` 池行新增键，纯增量，无消费方被破坏（前端是唯一
  消费方，本 delta 同步消费）。
