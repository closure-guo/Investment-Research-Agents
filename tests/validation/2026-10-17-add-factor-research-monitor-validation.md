# 因子研发监控人工验证报告（add-factor-research-monitor）

- 日期：2026-10-17（采集器部署 12:10；后端/前端上线 12:19；页面实跑截图 12:19）
- 变更：`openspec/changes/add-factor-research-monitor`（采集器快照 + 只读端点 + `/factor-research` 监控页）
- E2E 门禁：默认套件 CI 语义（`DEEPSEEK_API_KEY`/`LLM_API_KEY` 清空 → @live 跳过；retries=1）
  **52 passed / 2 flaky（重试即绿）/ 0 failed**；新 spec `factor-research-monitor.spec.ts` 单跑 5/5
- 证据：`tests/validation/2026-10-17-add-factor-research-monitor-live.png`（生产栈实跑截图，1440×900 全页）

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 运行中状态与进度展示 | 是（spec #1） | 徽章/pid/循环·步骤·x/5/因子/日志尾/历史表 | 实跑截图：运行中 · pid 7752 · 循环 6 · 当前步骤 编码实现 · 第 2/5 步 · 本步已用 24分54秒/预计剩余 1小时39分 · 19 个因子 chips · 日志尾真实渲染 · 历史表 1 行「进行中」 | ✅ |
| 陈旧快照披露 | 是（spec #2） | 「数据已陈旧」提示 + 内容仍可见 | E2E 通过（fixture 回拨 400s → 真实端点派生 stale=true → 横幅可见） | ✅ |
| 未接入 / 损坏空态 | 是（spec #3/#4） | 两种空态文案区分、不冒充加载中/错误 | E2E 通过（删文件 → 「采集器未接入」；写垃圾 → 「快照损坏」） | ✅ |
| 入口同源（侧边栏收起态 + 命令面板） | 是（spec #5） | 两入口均进入 /factor-research | E2E 通过 | ✅ |
| 只读纪律 | — | 页面零写入口 | 截图核查：无启停/控制按钮 | ✅ |
| 真实数据链路（distro 采集器 → /mnt/d → 后端容器 → 页面） | 否（生产实跑；E2E 为 fixture 种子） | 真实研发进程数据可见且与 distro 侧实测一致 | 端点探针 + 截图：supervisor pid 7752 / 循环 6 coding / 19 因子名 / 候选实现 144 —— 与 distro 侧状态脚本及 workspace 实测逐项一致 | ✅ |
| 采集器增量解析正确性 | 单测（19/19） | 因子名去重保序/会话切换重置/未知置空 | 生产实跑因子列表与 workspace 实现名 **19/19 逐字一致**（交叉验证，见下异常记录 3） | ✅ |
| stop 流程末次快照（running=false）+ 收采集器 | 否 | stop 写末次快照后收采集器 | **未实跑**（不停止在跑的研发会话，避免打断「不说停就一直研发」语义）；`--once` 单测覆盖 + 脚本逻辑审查；待用户实际停止时补验 | ⏸ |

## 环境与过程记录

- 采集器：12:10 推入 distro（`/root/collect_factor_status.py` + 更新后的 start/stop 脚本），daemon pid 9776，
  30s 周期；快照落 `/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/status.json`。
- 后端/前端：12:19 `docker compose up -d backend frontend`——dev override 挂载 `./src`（--reload）与
  `./frontend`（vite 源挂载），新端点/新页面随容器启动即生效，**无需重建镜像**（tasks 中「前端镜像重建」
  实际为启动即生效，未做 rebuild）。
- 端点探针：`GET /api/factor-research/status` → 200、`available=true`、supervisor.running=true。
- 页面：Playwright CLI 截图（`npx playwright screenshot --viewport-size=1440,900 --full-page`）。

## 异常记录

1. **本地全量默认套件首跑 flake（与本变更无关）**：两次首跑分别出现 screening-page / session-switch-resumption /
   broker-console 家族的 load-flake（互不重复、单跑全绿、spec 内注释已自认「8 并发 worker + 单进程后端下偶见
   >5s」）。本变更的 spec 在两次全量运行中均绿；CI retries=1 语义下全绿（2 flaky 重试即绿）。
2. **@live 误跑（环境差异，非仓库缺陷）**：本地 shell 环境存在用户级 `DEEPSEEK_API_KEY` → @live 用例未跳过、
   打 stub 后端而失败；以 keys 清空复跑对齐 CI（CI 无该变量；@live 由 nightly 单独跑）。
3. **因子名语义交叉验证**：会话日志 `factor_name:` 出现 431 次、去重 19 名——与 workspace 实现
   （43 个尝试目录、19 个 `calculate_*` 函数）逐字比对一致。结论：RD-Agent 每循环**批量提出 5 个因子**，
   19 名即本会话实际提出的因子全集，采集器抽取无「检索语料/历史因子」污染。

## 结论

- [x] 通过（stop 流程一项挂账：待用户实际停止研发时补验；属运维路径，非本页功能缺陷）
