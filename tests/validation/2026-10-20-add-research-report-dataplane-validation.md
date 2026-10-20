# 验证报告：add-research-report-dataplane

- **日期**：2026-10-20
- **验证人**：ZCode（agent）
- **关联 delta**：`openspec/changes/add-research-report-dataplane/`
- **工作区**：`.worktrees/research-report-dataplane`（分支 `feat/research-report-dataplane`）
- **E2E 门禁**：不适用（本 delta 无前端/SSE/会话状态变更；后端取数与上下文变更由单测与实跑覆盖）

## 1. 实测环境

- 数据面库：`data/research_reports.db`（工作区内，未触碰主检出开发库）
- 免费源：东财 `reportapi`（五类）、巨潮评级预测（akshare）、东财全市场盈利预测（akshare）、新浪研报频道（计数交叉）
- 采集限流：东财域名串行 + ≥1s 间隔 + 抖动（`EMRateLimiter`）

## 2. 实跑读数

### 2.1 日批（as_of=2026-10-19，窗口 2 天）

| 项目 | 读数 |
|---|---|
| 研报元数据 | 220 条（stock 28 / industry 118 / strategy 33 / macro 31 / morning 10），入库 218 条（同批内 2 条同键合并） |
| 评级变动 | 42 条（巨潮当日全市场） |
| 一致预期快照 | 2887 条处理 → 2426 个唯一标的落库 |
| 运行记录 | 三 job 全 `ok`；新浪计数 company/industry/strategy/macro 各 40（列表页截断值，仅作「有/无」级交叉） |
| 错误 | 0 |

### 2.2 历史回填（两个月抽验）

| 窗口 | 行数 | 分类型 |
|---|---|---|
| 2026-09 | 3053 | stock 1043 / industry 1130 / strategy 345 / macro 288 / morning 247 |
| 2025-04 | 6304 | stock 3543 / industry 1752 / strategy 355 / macro 401 / morning 253 |

回填后主表累计 **9539** 行；同窗口重跑幂等（日批与回填重叠行按键合并，无重复膨胀）。

### 2.3 覆盖状态三态（真实标的）

| 标的 | 状态 | 最新报告 | 近 30 日 | 总条数 | age_days |
|---|---|---|---|---|---|
| 600519 贵州茅台 | `active` | 2026-09-21 | 1 | 25 | 29 |
| 600845 宝信软件 | `stale` | 2025-04-10 | 0 | 2 | 558 |
| 999999（不存在） | `no_coverage` | — | 0 | 0 | — |

`collection_failed` 态由单测覆盖（无行 + 最近一次研报日批 failed）。

### 2.4 一致预期（东财盈利预测快照）

- 600519：`covered`，研报数 43，评级分布 买入 34 / 增持 9，EPS 2026E 67.2493 / 2027E 71.1259 / 2028E 75.18；
- 600845：`covered`，研报数 4，买入 3；
- 快照「当年起三年」口径验证：东财表内仍含 2025E 列，落库已按当前年份过滤（修复前 eps_y1_year=2025，修复后=2026）。

### 2.5 手工投喂（真实 PDF 实跑）

| 项目 | 读数 |
|---|---|
| 投喂文件 | `宝信软件_600845_20261019_190213_report.pdf`（本机既有报告 PDF，1.3 MB） |
| 首次扫描 | scanned 1 / ingested 1 / skipped 0，运行状态 `ok` |
| 解析结果 | 14 页、13,215 字符；中文全文可读（抽头为报告标题与技术面摘要） |
| 元数据推断 | 文件名推断发布日期 2026-10-19；机构无 `-` 分隔 → 留空（不编造） |
| 重复投喂 | 第二次扫描 scanned 1 / ingested 0 / skipped 1，文档表仍 1 行（哈希幂等） |
| usage_policy | `personal_research` |

### 2.6 LLM 结构化抽取与按标的合并（真实 PDF 实跑）

| 项目 | 读数 |
|---|---|
| 抽取样本 | 两份真实 PDF（宝信软件 600845 / 南方航空 600029，各 14 页量级） |
| 抽取结果 | **2/2 成功**，0 错误；单篇输出约 1.3k 字符 JSON |
| 600845 合并 | 1 篇；评级归一化「看空 → 卖出」；目标价 18.53（报告目标带中值）；核心逻辑/催化剂/风险各 5 条 |
| 600029 合并 | 1 篇；评级「卖出」；观点清单完整抽取 |
| 幂等 | `extract_all` 二次运行 skipped 2（已有抽取不重复调用 LLM）；`--force` 可重抽 |
| 失败降级 | LLM 不可用时文档照常入库、运行记录记 degraded（单测覆盖） |
| 备注 | 本次样本为本系统自产报告（非卖方 PDF），用于验证「解析→抽取→合并→上下文」链路；抽取字段完整性以真实卖方报告另行抽验 |

## 3. 自动化验证

| 项 | 命令 | 结果 |
|---|---|---|
| 数据面存储（含投喂文档/抽取表） | `pytest tests/data/test_research_reports_store.py` | 18 passed |
| 采集器（含投喂扫描/抽取/合并） | `pytest tests/data/test_research_reports_collectors.py` | 22 passed |
| 研报取数契约（真实快照 fixture） | `pytest tests/test_data_coverage_fetchers.py` | passed |
| 分析师上下文（含一致预期三态） | `pytest tests/nodes/test_analysts.py` | passed |
| 调度注册（镜像守护 + 数量 + misfire） | `pytest tests/outcome/test_scheduler.py tests/outcome/test_ops_jobs.py` | passed |
| 全量后端 | 见 §4 | 见 §4 |
| Lint | `ruff check .` | All checks passed |
| 类型检查（本 delta 文件） | `mypy <6 个改动模块>` | 本 delta 文件 0 error（仓库存量 mypy 错误为既有非门禁项） |

## 4. 全量 pytest

命令：`PYTHONPATH=src python -m pytest -m "not live" -q`（工作区，最终版含投喂/抽取/合并，耗时 858s）

结果：**6350 passed / 6 skipped / 12 deselected；3 failed + 25 errors**。

失败与错误全部集中在 `tests/test_deploy_script.py` 与 `tests/test_prompt_history.py`（脚本模式 CLI / deploy 测试），根因是该组测试 fixture 执行真实 `git push -qu origin main` 返回 128（本机 git 远端环境问题）；**在主检出（未含本 delta 改动）同命令复现相同失败**，属基线环境红，与本 delta 无关。

## 5. 浏览器 E2E（Playwright stub 套件）

- 栈：工作区后端（`uvicorn`，`TESTING=1` + 全套隔离库含 `RESEARCH_REPORTS_DB_PATH`）+ 前端（Vite 5173）；Playwright 1.62。
- 全量默认套件：**40 passed / 5 failed / 5 flaky / 22 skipped / 18 did not run**（6.1 分钟）。
- `daily-ops` 失败：时间线任务数硬编码 20，新增研报数据面 4 个 job 后实际 24 → **更新 spec 后单测通过（3.3s）**；该用例同时证明了 4 个新 job 在运维页按时刻升序渲染、五态徽章齐全（端到端验证了新调度接线）。
- 其余 4 个失败：3 个（`agui-chat` 刷新恢复、`broker-console` 持仓、`factor-research-monitor` 运行中）单独复跑通过 → 并发/负载型 flake（#328 族）；1 个 `agui-session-switch-isolation` 连续失败，**用主检出（不含本 delta）起同一套栈复跑同样失败** → 既有问题，与本 delta 无关。

## 6. 未完成 / 遗留

1. **prompt 已发布（2026-10-20 补记）**：`fundamental_analyst.md`（一致预期消费）与 `report_extraction.md`（抽取合同）已经 `scripts/deploy_prompts.py` 发布到 Langfuse production；复跑预检两文件均显示「与 Langfuse production 内容一致」。同批 `risk_judge` / `trader` 因 Langfuse 侧存量不一致被预检排除，与本 delta 无关。（tasks 5.3 / 8.5 已勾）
2. **新浪仅四类**：实测新浪研报频道无券商晨报独立分类；晨报交叉只依赖东财单源（已写入 spec）。
3. **新浪计数按页截断**（40 条/页）：只能做「有/无」交叉，不能做精确对账（spec 已明确）。
4. **全量历史回填**（2024-01 起）未跑：本次只抽验两个月；正式启用前建议在容器内跑全量（约 20 个月 × 五类，受 ≥1s 限流约束）。
5. **付费备选未接**：Tushare 摘要 / AShareHub / 终端仅登记（proposal Impact），本 delta 不接。
6. **E2E 既有失败**：`agui-session-switch-isolation` 为主检出同样复现的存量问题，建议另立 issue 跟踪。
