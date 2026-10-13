# Tasks: add-paper-broker

## 1. Broker 抽象与 paper 实现
- [x] 1.1 base.py 抽象接口 + paper.py（撮合走 matching-engine）+ 全链路单测（T1 @1237999a；`tests/execution/test_paper_broker.py` 全绿，spec 场景「下单闭环」「涨跌停挂单」逐字钉死）
- [x] 1.2 持仓/资金 SQLite 落库 + 幂等结算单测（T2 @94ddadf4；orders/positions/cash + paper_equity 四表；幂等 upsert 用例全绿）

## 2. 战绩对照
- [x] 2.1 仪表盘虚拟 vs 仿真双曲线 + 执行损耗标注 + 前端 Vitest（T3 @6fd58e5e；`frontend/src/test/paperCurve.test.tsx` 3/3：双序列值/图例「虚拟结算」「仿真成交」DOM/`执行损耗: -1.2%`；`paper: null` 与字段缺省两态单曲线不报错）
- [x] 2.2 README 免责声明增补（顶部引言后引用块，逐字含「仅供研究，不构成投资建议」；研究性口径披露；diff 最小=+1 行）

## 3. 验证
- [x] 3.1 全量测试通过（后端 `uv run pytest tests/ -m "not live" -q` → 1 failed / 5343 passed / 6 skipped（唯一失败 = `tests/llm/test_resolver.py` 的 .env 泄漏既有机器级用例，单跑通过、非本 change 引入，见验证报告异常记录 3b）；`tests/execution tests/outcome` 783 passed、`tests/test_api_track_record.py` 37 passed；前端 82 files/688 tests 绿 + `npm run build` 通过；E2E 新增 spec 2/2、所属隔离套件 16/16 绿。默认 config 全量 E2E 因 8000/5173 被本机 docker compose 占用、按端口预检纪律跳过——如实记录，见 `tests/validation/2026-10-13-add-paper-broker-validation.md` 异常记录 2）
- [x] 3.2 一个月回放 10 笔成交人工核对，报告落 tests/validation/（T4：合成面板 2026-09 全月 13 笔成交，逐笔价格/费用 vs 脚本内独立手算 **13/13 一致**；资金台账 1,057,821.46 期望=实际、期末空仓、一字涨停挂单 pending 零变动；产物 `tests/validation/2026-10-13-add-paper-broker-replay-check.{py,json}` + 报告。**2026-09 真实行情回放受东财 IP 封禁 issue #325 阻断 → 合成替代，未计真实回放通过**（报告「回放核对」诚实边界））
- [x] 3.3 既有测试无修改通过（红线）（T3/T4 未改任何既有测试语义：`tests/test_api_track_record.py` 仅**追加** 1 例 + import 名单增 1 名；前端既有测试零修改、新断言只追加；T2 的 ops 注册表计数 7→8 为计划明示允许的最小更新。`tests/execution tests/outcome` 783 passed 与改动前同集）

> **备注（T4 落地偏差，详见验证报告）**：
> 1. E2E 落位改用 `playwright.track-record.config.ts`（8004/5177 + SESSIONS/EXECUTION 双独立库）——战绩造数 spec 走默认 config 会与 `decisions.spec.ts` 空态断言互斥（既有 review fix 已判例在案）；文件路径仍为计划原样 `tests/e2e/playwright/tests/paper-broker.spec.ts`，运行命令 `npx playwright test -c playwright.track-record.config.ts tests/paper-broker.spec.ts`。
> 2. 新增 TESTING seed 复位键 `track_record_reset`（清 equity_curve + paper_equity；派单要求 beforeEach/afterEach reset 而此前无清理通道），`tests/test_api_track_record.py` 追加 1 例覆盖。
> 3. 人工终审签字 pending（交互类变更门禁：图例/损耗标注可读性、README 措辞与位置需人工确认）。
