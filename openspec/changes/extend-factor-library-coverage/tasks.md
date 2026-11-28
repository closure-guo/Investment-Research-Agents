# Tasks: extend-factor-library-coverage

> 分三段交付：**P1** 算子与计数口径（§2–§5）→ **P2** 参考数据面与字段（§6–§11）→ **P3** 提案侧与存量重放（§12–§13）。每段独立可验证；P1 不依赖任何回填。
> 实施纪律：每项先写失败测试再实现；删改公共符号须四目录（src/tests/evals/scripts）全 grep。

## 1. 前置与基线

- [x] 1.1 冻结基线读数并落 `tests/validation/`：库实态（12 active 的表达式与 60 日 IC）、漏斗（2444 → 1772 不可译 → 653 进闸 → 12 入库）、不可译分桶、最近一个成功选股批次的 Top-100 排序
- [x] 1.2 `docs/factor-governance.md` 追加修订记录：自由参数计数口径由「Number 节点总数」改为「去重数值个数」，上限 3 不变（D4 裁决三的口径修订，附 398 条实测依据）
- [x] 1.3 确认 `update-harvest-factor-category` 的生产迁移已执行（§10 桶归属的前提；未执行则先执行其遗留任务）——复核：**未执行**（category 仍为 `外部×12`，见基线文档 §5）；执行随 §10 前移（P2）。**2026-11-28 已执行**：容器内 `scripts/migrate_factor_category.py` migrated=12（幂等复跑 migrated=0）；active 12 条桶分布 = 量与流动性 5 / 波动率 3 / 量价交互 1 / 外部-其他 1 / 价量 1 / 流动性 1

## 2. DSL：自由参数去重计数（P1）

- [x] 2.1 失败测试（`tests/factors/test_dsl.py`）：同取值重复出现的表达式解析成功、去重后仍超上限拒收、`1` 与 `1.0` 计一次、错误消息格式不变
- [x] 2.2 实现 `count_free_params` 按去重取值计数；`docs`/docstring 的 D4 口径同步
- [x] 2.3 四目录 grep 计数口径的全部消费方并同步（提示词模板、契约块措辞、既有断言）——契约块数值 import 自真相源无需改；**提示词模板措辞随 §12.2 统一改并一次 deploy**（P1 不动 Langfuse 快照，避免双次发布）
- [x] 2.4 `tests/factors` 全绿 + ruff + mypy

## 3. DSL：比较与条件算子（P1）

- [x] 3.1 失败测试：六个比较算子与 `if_else` 的解析、求值、NaN 传播、事件计数与零分母保护两个场景
- [x] 3.2 实现算子表（`ARITY`）与求值（比较结果为 1/0 浮点帧，NaN 显式传播）
- [x] 3.3 `qlib_translate` 补 `Greater/Less/Gt/Ge/Lt/Le/Eq/Ne/If` 映射，逐值等价核对，既有映射回归全绿
- [x] 3.4 翻译器支持对括号表达式的一元负号（严格等价改写 `sub(0,x)`），既有负数字面量规则回归不变

## 4. DSL：时序统计与计数算子（P1）

- [x] 4.1 失败测试：`ts_median` / `ts_quantile`（含 q 越界解析期拒收）/ `ts_count` / `ts_skew` / `ts_kurt` / `ts_decay_linear` / `ts_arg_max` / `ts_arg_min` 的规范场景
- [x] 4.2 实现各算子（向量化；`ts_skew` / `ts_kurt` 与 pandas rolling 逐值一致；`ts_arg_*` 按行分块控内存）
- [x] 4.3 `qlib_translate` 补 `Med/Quantile/Count/Skew/Kurt/WMA` 映射，既有映射回归全绿
- [x] 4.4 性能预算测试：500 × 5500 面板上各新算子单次求值不超过 `ts_rank` 同量级（断言上限）

## 5. DSL：回归与横截面算子（P1）

- [x] 5.1 失败测试：`ts_resid` / `cs_mean` / `ind_neutralize` / `exp` 的规范场景（含面板未携带 `industry` 分组时求值报错）
- [x] 5.2 实现 `ts_resid`（滚动均值 / 协方差闭式）、`cs_mean`、`ind_neutralize`、`exp`
- [x] 5.3 `dsl.py` / `eval_engine.py` 模块文档的算子枚举与口径说明同步

## 6. 参考数据面：侧表与读路径（P2）

- [x] 6.1 失败测试：`market_ref_daily` DDL 幂等、`security_master.industry_l1` 列迁移幂等、主表 upsert 不把已有行业覆盖为空
- [x] 6.2 实现侧表 DDL（仅 `init_snapshot_db` 写路径）与主表行业列迁移
- [x] 6.3 失败测试：`read_universe` / `read_universe_range` 附加列、区间读取仍单次 SQL、`listed_days` 缺失时由 `list_date` 派生、旧库无侧表退化逐字一致
- [x] 6.4 实现读路径 LEFT JOIN 与旧库退化（侧表缺失 MUST NOT 被误判为 `SnapshotEmptyError`）
- [x] 6.5 回归：三处面板加载器（`monthly` / `monitor` / `screening.pipeline`）在附加列之外逐值不变

## 7. 参考数据面：面板可选字段与分组（P2）

- [x] 7.1 失败测试：面板构造 `float_cap` / `turnover` / `listed_days` 三帧与 `industry` 分组、行不携带时不构造、缺失不前向填充
- [x] 7.2 实现 `build_panel_from_rows` 的可选字段与分组构造（停牌行口径与价量字段的差异写明）
- [x] 7.3 DSL 字段集加入三个可选参考字段并补解析测试（计入字段种类数）

## 8. 参考数据面：增量落库（P2）

- [x] 8.1 失败测试：腾讯批量解析返回逐票流通市值 / 总市值 / 换手率；东财 spot 路径取换手率；既有调用方返回值兼容
- [x] 8.2 扩展 `fetch_market_caps_bulk`（或新增兄弟函数），换手率统一小数口径
- [x] 8.3 失败测试：`regime_daily` 落侧表成功 / 写入失败仅告警 / 快照不可用不写；拥挤度行与失败语义零回退
- [x] 8.4 实现侧表 best-effort upsert 接线
- [x] 8.5 失败测试：周一主表刷新分支对缺行业代码增量补抓、失败不拖垮整批、不覆盖已有值
- [x] 8.6 实现主表行业增量补抓

## 9. 参考数据面：历史回填（P2）

- [x] 9.1 实现参考数据回填脚本（新浪 `outstanding_share` / `turnover`；显式库路径与日期区间；幂等；护栏同快照回填；摘要含行数 / 票数 / 失败数 / 日期区间）
- [x] 9.2 脚本单测：回填行口径、源缺列按失败、幂等、不改 `market_snapshot`
- [ ] 9.3 容器内执行参考数据历史回填（日批窗口外），核对日期覆盖区间、覆盖率，并抽样与腾讯当日值比对
- [ ] 9.4 行业门类：50 票探针（成功率 ≥ 90% 方可全量）→ 全量回填 → 覆盖率读数落验证报告

## 10. 准入与治理（P2）

- [x] 10.1 失败测试：引用未携带字段的候选在闸①拒收（`LOW_COVERAGE` + `detail.missing_fields`）、整批不中断、落 `rejected` 行
- [x] 10.2 实现 `admit_candidates` 阶段 A 的字段 / 分组预检（求值前）
- [x] 10.3 失败测试：同构重提桶归属两个场景
- [x] 10.4 实现入库前桶归属沿用与事件 `detail.inherited_category_from`
- [x] 10.5 失败测试：builtin 七条——参考数据缺失时两条闸①拒收、到位后过同一五道闸、幂等 `skipped=7`
- [x] 10.6 builtin 包扩为七条（名称 / 表达式逐字）；风格篮子随之扩展，风格暴露诊断批实跑无异常
- [x] 10.7 更新 `tests/factors` 中按五条基线写的计数断言与前端标签回退的既有测试核对（无前端代码改动）

## 11. 选股披露（P2）

- [x] 11.1 失败测试：as_of 截面缺口因子进 `factor_value_gaps`、排序与「该因子不参与」逐项一致；无缺口为空列表
- [x] 11.2 实现 L2 有效值统计与选股日批摘要字段

## 12. 提案侧（P3）

- [x] 12.1 失败测试：`CATEGORIES` 九类、枚举外取值仍走修复重试、提示词模板与 `factors.dsl` 真相源一致
- [x] 12.2 `CATEGORIES` 扩展；`prompts/factor_proposal.md` 同步算子 / 字段 / 类别 / 计数口径
- [x] 12.3 `uv run python scripts/deploy_prompts.py` 发布，prompt-deploy-consistency 门禁通过
- [x] 12.4 核对 distro 生成侧补丁（#510）对自由参数的措辞与新口径一致（生成器恢复前完成）

## 13. 验证与收口

- [x] 13.1 P1 上线后执行**离线确定性复译测量**（`tests/scripts/measure_dsl_unlock.py`：对状态文件已记录的 translator 阶段 qlib 表达式重译 + 复杂度复验，零 LLM、零生产写入；生产重放脚本每条都重走 LLM 转译且非确定性，另行确认）；解锁条数与分桶落 `tests/validation/`
- [ ] 13.2 人工验证报告 `tests/validation/YYYY-MM-DD-extend-factor-library-coverage-validation.md`：回填后真实读数（`float_cap` / `turnover` 覆盖率、与腾讯抽样比对）、两条真实口径基线的过闸结论、L2 缺口披露、存量 12 条 active 读数不变
- [ ] 13.3 全量门禁：`uv run pytest` / `uv run ruff check` / `uv run ruff format --check` / `uv run mypy`
- [ ] 13.4 研究笔记回填「已落地」状态
- [ ] 13.5 `openspec validate extend-factor-library-coverage --strict` 通过；交互类判定复核（无前端 / SSE / 会话变更）
