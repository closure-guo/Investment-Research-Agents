# AGENTS.md — Investment Research Agents

LangGraph 多 Agent A 股分析系统：FastAPI 后端 + React 18/Vite 前端。输入股票代码，输出多 Agent 辩论式交易决策报告。

---

## 会话启动（每次会话开始必须先执行）

按以下顺序加载前置依赖：

1. 调用 `using-superpowers` 技能 → 加载 Superpowers 工作流体系
2. 读取 `docs/project-workflow.md` → 获取本项目执行 SOP 地图

---

## 架构速览

- 五层流水线：4 分析师并行 → Bull/Bear 辩论 → Trader 决策 → 风控辩论 → 基金经理批准
- 后端 `src/finance_agent/`（nodes/metrics/data/events/export/harness/prompts）
- 前端 `frontend/`（React + TS + Tailwind + ECharts）
- 链路追踪：Langfuse `http://localhost:3000`
- StreamRegistry 为进程内内存结构 → 后端必须单 uvicorn worker（不可 `--workers N`）
- 架构详情见 `docs/architecture.md`；ADR 见 `docs/adr/`（编号递增；agent 可新建/编辑，改既有结论须在文末追加修订记录，不得静默改写历史）

---

## 常用命令

```bash
docker compose up -d --build          # 全栈启动（重建前先查 /api/sessions 无 running 会话，见红线）
uv run uvicorn finance_agent.api:app --host 127.0.0.1 --port 8000 --reload  # 后端
cd frontend && npm run dev            # 前端
uv run pytest                         # 后端测试
uv run ruff check                     # Lint
uv run mypy                           # 类型检查
cd frontend && npm test               # 前端测试
```

---

## 任务路由

`project-workflow.md` 是本文档引用的执行 SOP 地图。按下表判定任务类型后，直接跳转该文件对应章节执行。

| 任务类型 | 判别方法 | 动作 | 执行地图 |
|---|---|---|---|
| 新功能 / 行为变更 | 系统新增能力，或 `openspec/specs/` 中行为未定义 | OpenSpec delta + Superpowers | `project-workflow.md` §3 |
| 修 bug · 意图不变 | `openspec/specs/` 已写明正确行为，代码未做到 | systematic-debugging + 复现测试 | `project-workflow.md` §4.1 |
| 修 bug · 意图变更 | `openspec/specs/` 无对应条目，或条目需修改 | 同新功能 | `project-workflow.md` §4.2 |
| 重大架构决策 | 涉及架构层面取舍 | 落 `docs/adr/`（agent 可新建；改既有 ADR 须追加修订记录） | — |
| 小改动 | typo / 文案 / 配置 | 直接改 | — |

**交互类变更判别**：delta 涉及前端 UI、SSE 流式、会话切换、状态流转中任一者 → 走 `project-workflow.md` §3 完整管线（含 E2E 门禁）。

---

## 红线（检查清单，不展开）

- [ ] `openspec/specs/` 是系统行为的唯一真相来源；改动前先查；只经 delta 编辑、sync 合并，禁止手改主规范库
- [ ] 没有先写失败测试的代码 → 删除重写
- [ ] 「测试全过」≠「行为正确」；交互行为变更必须有人工验证环节
- [ ] E2E 禁止 mock 被测系统（`route.fulfill` / MSW 拦截业务接口响应 = 红线）；LLM/第三方 API 可用 `TESTING=1` stub，但须配 `@live` 用例 nightly 防漂移
- [ ] archive 前置条件：`tasks.md` 全勾 + verification 通过 + E2E 门禁通过（交互类适用）+ 人工验证报告落 `tests/validation/`
- [ ] 排查 bug 必须同时查后端日志和 Langfuse trace
- [ ] 重建/重启后端（`docker compose up -d --build` / restart）前先查 `GET /api/sessions` 无 `status=running` 会话；有运行中分析须等其完成或经用户确认再动——宿主机改码不触发容器内 `--reload`（WSL2 bind mount 的 inotify 不传播），手动重建撞上运行中分析会把它杀成 interrupted（2026-10-04 一日三例）
- [ ] 交易日 15:30–17:10（日批窗口）与 17:55–20:45（cohort/日报）不重建/重启后端——进程内日批被杀即当日断供，且「跑了一半被杀」的僵尸 running 行会被启动补跑的 running-skip 规则挡住、6h 窗内无人接管（2026-10-21~23 连发三例，issue #455）；重启前除查会话外，查 `GET /api/v1/ops/jobs` 无 running 作业
- [ ] 系统性问题记录到 `docs/incidents/`（编号文档 + 更新 README 索引）
- [ ] 指标低 ≠ 能力差：任何聚合指标进入处置（重试 / 改 prompt / 判定 agent 缺陷 / 写进报告结论）前，必须先分桶归因、逐条人工终裁；自动化处置只允许挂在终裁为「真错误」的桶上（incident 026）
- [ ] Issue 一律用 `gh` CLI；标签规范见 `docs/agents/triage-labels.md`
- [ ] 密钥永不进仓：新增 provider key 形态先补扫描器（`KEY_PATTERNS` + 测试），轮换/泄露处置按 `docs/agents/secret-hygiene.md`（四层防线 + 先轮换 doctrine）

---

## 测试约束

- E2E（真实浏览器）和集成测试（直连 API）分工明确，详见 `project-workflow.md` §5.6
- 产物位置：fixtures → `tests/fixtures/`｜脚本 → `scripts/`（部署类）与 `tests/scripts/`（测试辅助）｜验证报告 → `tests/validation/`｜E2E 输出 → `tests/e2e/playwright/`｜报告 → `reports/`
- 提示词权威源：`src/finance_agent/prompts/*.md`（git 跟踪）是唯一权威源，Langfuse 为部署产物快照；修改 prompt 后必须执行 `uv run python scripts/deploy_prompts.py` 发布，否则 eval 门禁拒绝运行（见 openspec specs/prompt-deploy-consistency）

---

## 评估约束

**指标存放**（唯一查询入口，不得另建副本）：

- `docs/evals/metrics.md`：指标台账——口径定义（每个指标的定义 / 代码位置 / rubric 版本 / 基线切点）+ 每轮实验时间线 + 待终裁与待决策清单。**口径变更先改 §1 再动代码**
- `docs/evals/metrics/runs.jsonl`：每轮实验一行的机器可读摘要（run / HEAD@启动 / means）
- Langfuse Scores = 逐 trace 明细真源；`reports/evals/*.json` = 单次实验全量产物（本地，不入库）
- 校准数据 → `evals/judge_calibration/data/`｜校准与复盘报告 → `docs/evals/`｜归因 / 终裁对照表 → `tests/validation/`

**评估 SOP**（一次只动一个变量）：

1. **先修 judge 输入，再标注**：交标注表前必须过三道关——①逐行通读材料（程序查在不在，人查可不可信）；②跨层一致性（每层看到的 = 上游产出的、judge 变量 = state、材料数字可溯源到上游章节）；③整齐得可疑的数字（全 5 分 / 全 0.55 / 全 4/8）先解释再放行。三关未过不得交表
2. **指标异常先归因**：按桶分解 → 逐条人工终裁 → 处置对象必须匹配归因桶（契约病修契约、解析病修解析器、真幻觉才修分析师）；混合指标须拆报，不得把校验器误报读成分析师幻觉
3. **实验收口**：健康检查（分数齐全度 / 解析失败率 / confidence 落库率）→ 契约抽验 → `metrics.md` 时间线加一行 + `runs.jsonl` 追加 → 校准报告落 `docs/evals/`
4. **观测数据会撒谎**：根 span 元数据会被覆盖、兜底文案会谎称「数据缺失」——排查必须回到节点原始输入/输出，LLM 在理由里抱怨输入缺失是比分数更强的信号
5. 人工标注是 judge 的终裁：judge 分零方差的维度不能算 Spearman，以 MAE / 方向一致率为主
