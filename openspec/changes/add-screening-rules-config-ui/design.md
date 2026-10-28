# Design: add-screening-rules-config-ui

## Approach

**配置层**（新建 `src/finance_agent/screening/rules_config.py`）完全镜像
`analysis_concurrency.py` 的成熟模式：

- `resolve() -> dict`：逐项解析 `min_trading_days` / `min_avg_amount_20d` / `l2_top_n`，
  每项返回 `{value, source, default, min, max, env_value}`；另带文件级 `file_status`
  （missing / ok / corrupt / invalid）与 `updated_at`。
- 来源优先级逐项独立：配置文件该字段合法值 > 对应环境变量 > 默认值（默认值取
  `constraints.MIN_TRADING_DAYS` / `constraints.MIN_AVG_AMOUNT_20D` / `pipeline.L2_TOP_N`，
  经 import 消费避免第二份字面量——依赖方向是 rules_config → constraints/pipeline 常量，
  pipeline 对 `l2_top_n` 的默认值反向 import 会成环，故 `l2_top_n` 默认值在 rules_config
  内声明为 100 并由测试钉住与 `pipeline.L2_TOP_N` 相等）。
- `save(payload) -> dict`：三项整体校验（整数/数值类型、区间、bool 排除——bool 是 int
  子类须先排除，沿 analysis_concurrency `_valid` 写法），任一非法抛 `ValueError`
  （端点转 400）；合法则原子写（tmp + `os.replace`）单文件
  `data/settings/screening_rules.json`，路径可经 `SCREENING_RULES_CONFIG_PATH` 覆盖。
- 环境变量：`SCREENING_MIN_TRADING_DAYS` / `SCREENING_MIN_AVG_AMOUNT_20D` /
  `SCREENING_L2_TOP_N`，非法值 warn 后忽略（同 analysis_concurrency `_env_value`）。

**管道接线**（`pipeline.run_screening_batch`）：

```
批次开始（参数校验后、快照确认前）:
    rules = rules_config.resolve()          # 仅主档
    top_n = rules["l2_top_n"]["value"]
    thresholds = FilterThresholds(
        min_trading_days=rules["min_trading_days"]["value"],
        min_avg_amount_20d=rules["min_avg_amount_20d"]["value"],
    )                                        # 仅当调用方未显式传 thresholds
```

- 显式 `thresholds` 参数优先级**高于**配置（小票通道 `run_microcap_lane_daily` 传
  `MICROCAP_THRESHOLDS`，行为不变）；主档调用路径（`thresholds=None`）改读配置。
  注意 `run_microcap_lane_daily` 内部调用主 `run_screening_batch` 时必须继续显式传
  thresholds——配置解析只发生在「thresholds is None」分支，microcap 路径天然绕开。
- `L2_TOP_N` 三处读取点（ranker 调用与截断）替换为局部 `top_n`；模块常量保留并作为
  `l2_top_n` 的默认值来源（单一事实，测试钉住）。
- 解析时机 = 批次开始一次；批次内不再读取 → 运行中批次不受保存影响。
- **不加** `rules` 入参到 `run_screening_batch` 签名——配置是进程级状态，测试通过
  monkeypatch `rules_config.resolve` 或写临时文件注入，避免签名扩张。

**API 层**（`api.py`）对齐并发设置端点：`GET` 直返 resolve；`PUT` 以 `value: Any` 风格
接收整 dict，交 `rules_config.save` 校验，`ValueError → 400`。

**前端**（`frontend/src/pages/settings/panes/ScreeningRulesPane.tsx`）：

- 镜像 SessionsPane 并发设置区块的加载/保存/错误隔离模式，独立 fetch、失败只影响本分区。
- 成交额输入框单位为**万元**：加载时 `元 → 万元`（÷1e4）展示，保存时 `万元 → 元`（×1e4）
  后整体 PUT 三项；万元输入限非负整数（×1e4 精确无舍入）。
- 每项显示来源徽章（配置文件 / 环境变量 / 默认值）与区间；越界/非整数前端拦截
  MUST NOT 发请求；400 时原位展示后端 detail。
- 生效时机文案固定：「下一次选股日批或手动补跑开始时生效，运行中批次不受影响」。

## Alternatives Considered

- **配置放 /screening 页内联**：选股页是观察面，规则属低频配置；设置中心已是配置聚合地
  （8 个既有分区先例），且与并发设置模式同构。选设置中心，选股页不加编辑面。
- **每项一个独立端点**：三项规则语义同属「选股规则」，整体保存（单文件、单事务语义）
  比三次请求简单且原子；与并发设置的单值端点形态不同但复杂度更低。
- **稳定性门槛天数一并可配**：在飞 delta 正改 `store.days_in_top_recent` 签名，同域两
  delta 并行必撞；推迟到其归档后另行提案（proposal 已列入不做）。
- **L3 开关**：契约「单只失败按 keep 降级、批内不阻断」是行为契约非参数，做成开关会引入
  「关掉 L3 但批次 ok」的歧义语义，不做。

## Risks

- **默认值漂移**：`rules_config` 内 `l2_top_n` 默认值 100 与 `pipeline.L2_TOP_N` 是两处
  字面量 → 对策：规则配置模块 default 引用 constraints 常量（可 import 不成环），
  `l2_top_n` 由单测断言 `rules_config.DEFAULT_L2_TOP_N == pipeline.L2_TOP_N` 钉住。
- **万元换算精度**：×1e4/÷1e4 对整数万元精确；后端区间以元定义（1e6–1e9），前端区间以
  万元定义（100–100000），单测钉住两端点换算一致。
- **env 与容器**：生产环境 env 注入的差异会导致 UI 来源显示 env 而不可改写生效值
  （file 优先级更高，仍可覆盖）——spec 已要求披露 env_value，运维可判读；不额外做
  「env 锁定」逻辑。
- **e2e 稳定性**：设置分区 spec 复用既有 stub 后端与 seed 通道；保存动作用 data-testid
  定位，避免文本 selector。
