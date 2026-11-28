# factor-reference-data Specification

## Purpose
TBD - created by archiving change extend-factor-library-coverage. Update Purpose after archive.
## Requirements
### Requirement: 参考数据侧表与增量落库

系统 SHALL 在行情快照库内维护参考数据侧表 `market_ref_daily`（主键 `(trade_date, code)`；列：`float_cap` 流通市值（元）、`total_cap` 总市值（元，可空）、`turnover` 换手率（小数）、`source`、`updated_at`）。侧表 MUST NOT 改动 `market_snapshot` 既有行（快照行不可变语义不变）；侧表 DDL SHALL 只在写路径初始化（`init_snapshot_db`）执行，读路径 MUST NOT 建 / 改表。

增量写入 SHALL 复用拥挤度日批（`regime_daily`）的市值取数链：在该链取得逐票流通市值的同一次取数中一并取得换手率（东财 spot 列或腾讯批量行情字段），对当日行幂等 upsert；写入失败 SHALL 仅告警，MUST NOT 影响拥挤度日批自身的行产出与失败语义；MUST NOT 以成交额等替代口径冒充。快照不可用导致拥挤度日批跳过取数时 SHALL 不写侧表行（无行，而非 0 值行）。

证券主表 SHALL 携带行业门类列 `industry_l1`：一次性回填后，每周一的主表刷新分支 SHALL 对该列缺失的在册代码增量补抓（失败仅告警、不失败整批）；主表 upsert MUST NOT 把已有 `industry_l1` 覆盖为空。

#### Scenario: 侧表随拥挤度取数落库

- **GIVEN** 快照 as_of 新鲜、spot 无流通市值列、腾讯批量行情返回 2 只票的流通市值与换手率
- **WHEN** 拥挤度日批运行
- **THEN** `market_ref_daily` 有 as_of 的两行（`float_cap` / `turnover` 为源返回真值，`source="tencent"`）
- **AND** 同日重跑不产生重复行，且 `market_snapshot` 的 as_of 行逐字不变

#### Scenario: 落库失败不拖垮拥挤度

- **GIVEN** 侧表写入抛错
- **WHEN** 拥挤度日批运行
- **THEN** 拥挤度行照常产出、任务不失败，仅记告警日志

#### Scenario: 快照不可用不写侧表

- **GIVEN** as_of 晚于快照最新数据日期
- **WHEN** 拥挤度日批运行
- **THEN** 不发起取数、`market_ref_daily` 无 as_of 行（MUST NOT 写 0 值或占位行）

#### Scenario: 行业门类增量补抓且不被覆盖

- **GIVEN** 主表刷新分支运行；在册代码中某新上市代码 `industry_l1` 为空、其余代码已有值
- **WHEN** 主表刷新执行
- **THEN** 新代码被补抓，抓取成功则写入其行业门类
- **AND** 其余代码的 `industry_l1` 保持不变；补抓失败只告警、整批不失败

### Requirement: 参考数据历史回填

系统 SHALL 提供参考数据历史回填脚本：逐票取新浪历史日线（不复权口径）的 `outstanding_share`（流通股本）与 `turnover`（换手率），按 `float_cap = outstanding_share × 当日不复权收盘价` 派生流通市值，换手率归一为小数；只写 `market_ref_daily`，MUST NOT 修改 `market_snapshot` 行。回填 SHALL 幂等（重跑覆盖同键行）、显式指定库路径与日期区间、沿用快照回填的既有护栏（禁止在宿主机直跑活跃库）。源缺列的票 SHALL 计入 `symbols_failed` 留痕且不落半口径行，MUST NOT 以 NULL 或 0 值行冒充覆盖；摘要 SHALL 报告写入行数、覆盖票数、失败票数与日期覆盖区间。

#### Scenario: 回填行含流通市值与换手率

- **GIVEN** 新浪历史日线返回含 `outstanding_share` 与 `turnover` 列的不复权日线
- **WHEN** 回填执行
- **THEN** `market_ref_daily` 回填行 `float_cap` 等于流通股本 × 当日不复权收盘价、`turnover` 为源值（小数口径），`source` 标记为历史源
- **AND** 同区间 `market_snapshot` 行逐字不变

#### Scenario: 源缺列按失败处理

- **GIVEN** 某票历史数据源未返回 `outstanding_share` 或 `turnover` 列
- **WHEN** 回填执行
- **THEN** 该票计入 `symbols_failed` 留痕，不落 `float_cap` 或 `turnover` 为 NULL 的半口径行

#### Scenario: 回填幂等

- **WHEN** 对同一库与同一日期区间重复执行回填
- **THEN** 同键行被覆盖、行数不变，摘要的覆盖票数与首次一致

### Requirement: 读路径附加列与面板可选字段

`read_universe` 与 `read_universe_range` 返回的行 SHALL 附加可选参考列：`float_cap` / `turnover`（LEFT JOIN `market_ref_daily`，无对应行为 NULL）、`industry_l1`（LEFT JOIN `security_master`）、`listed_days`（快照行已有值则沿用，为 NULL 时由主表 `list_date` 与 `trade_date` 派生自然日差）；区间读取 SHALL 仍为单次 SQL。侧表或列不存在（尚未初始化的旧库）时读路径 SHALL 退化为不带附加列的查询，既有语义逐字不变。

`build_panel_from_rows` SHALL 在行携带上述列时构造面板可选字段 `float_cap` / `turnover` / `listed_days`（NULL → NaN，不复权口径）与 `industry` 分组帧（行业门类标签，缺失为 NaN）；行不携带时 SHALL NOT 构造（引用它们的表达式求值按因子 DSL 契约报错）。参考字段 SHALL NOT 前向填充，MUST NOT 以 0 冒充缺失。

#### Scenario: 行附参考列

- **GIVEN** 侧表有 as_of 某票一行（`float_cap=2.0e9`、`turnover=0.0125`），主表该票 `industry_l1="电子"`、`list_date="2024-01-10"`；快照 as_of 行该票 `listed_days` 为 NULL
- **WHEN** 调用 `read_universe(as_of)` 与覆盖 as_of 的 `read_universe_range`
- **THEN** 两种读法返回的该票行均含 `float_cap=2.0e9`、`turnover=0.0125`、`industry_l1="电子"`
- **AND** `listed_days` 为 as_of 与 `list_date` 的自然日差；无侧表行的票 `float_cap` / `turnover` 为 NULL

#### Scenario: 旧库无侧表读路径不报错

- **GIVEN** 快照库由旧版本创建、尚无 `market_ref_daily` 表与 `industry_l1` 列
- **WHEN** 调用 `read_universe` / `read_universe_range`
- **THEN** 返回行与升级前逐字一致（不含附加列），不报错、不建表

#### Scenario: 面板构造可选字段与分组

- **GIVEN** `read_universe_range` 返回的行携带 `float_cap` / `turnover` / `listed_days` / `industry_l1` 列
- **WHEN** 构造面板
- **THEN** 面板 `fields` 含 `float_cap` / `turnover` / `listed_days` 三帧（与价量六帧同 index / columns，NULL 格位为 NaN），分组帧 `industry` 为同形标签帧
- **AND** 行不携带这些列时三帧与分组帧均不构造，`log(float_cap)` 求值报「面板缺少字段」

#### Scenario: 缺失不前向填充

- **GIVEN** 某票 as_of 当日与前一交易日的侧表均无行，再前一交易日有行
- **WHEN** 面板构造并求值引用 `float_cap` 的表达式
- **THEN** 该票 as_of 当日与前一交易日的 `float_cap` 格位为 NaN（MUST NOT 用再前一交易日的值顶替）

