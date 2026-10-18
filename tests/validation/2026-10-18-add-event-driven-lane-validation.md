# 人工验证报告: add-event-driven-lane

**日期**: 2026-10-18
**验证人**: 自动化实施链（控制器 + 实施/评审 subagent 流水线）；**人工观感/分诊质量抽检待 owner**
**关联 delta**: openspec/changes/add-event-driven-lane/
**E2E 门禁**: 隔离 config 12/12（`market-regime` 3 + `event-watchlist` 3 + `screening-page` 6；含证伪自检）；默认套件 8000/5173 被本机 docker 生产栈占用，本地以 isolated config 代跑，**CI 兜底默认套件**
**UI 基线**: 选股页 screening light/dark 二次重采（事件观察区块空态 settle 钉定；sha 可复现；容差未动；其余 6 页未动）

## 验证结果（机器可判项）

| 项 | 证据 | 通过 |
|---|---|---|
| 后端回归 | `uv run pytest tests/outcome/ tests/test_api_events.py -q` → **795 passed** | ✅ |
| 静态检查 | `ruff check` 干净；`mypy` 事件包 + scorecards 零错误 | ✅ |
| 前端 | `npx tsc --noEmit` exit 0；`npx vitest run` → **100 files / 878 passed** | ✅ |
| E2E | 三 spec 12/12（事件区块三例 + 基线页 + 温度计）；证伪自检两轮 | ✅ |
| 规范校验 | `openspec validate add-event-driven-lane --strict` 通过 | ✅ |
| prompt 发布 | `event_triage.md` 已发布 Langfuse v1（内容核验一致；`--exclude` 未动无关 prompt） | ✅ |
| 评审链 | 后端（2 轮 + N1 复核）/ 前端（+ 补丁复核）/ E2E（+ 两处一行级收尾）**全部 Approved**，0 Critical 未闭环 | ✅ |

## 评审揪出的关键完整性问题（均已闭环）

1. **分诊输入前视（Critical）**：回填批次原共用「最新快照日」→ verdict 带事后行情（选择偏差）。改为**逐行** `≤ available_at` 最近快照日 + 缺失计数；强断言「更晚快照日不得出现」。
2. **容量槽取样契约破坏（Important, N1）**：结算降序泄漏到装载器 → 事件族容量取**最旧** 20 标的，与 caliber_note「最近 20 个」矛盾（评审实测复现）。一行排序修复 + 端到端对照测试。
3. **台账与实现三处矛盾**：§1.15 按实现定稿（入场只挡停牌/涨停挡买；OHLC 降级口径写明；递延结算与取数失败分列）；design 残句清理。
4. **停牌顺延留痕真实不可达**：改交易日历缺口识别 + 真实形态测试（无 kline 行 + `is_suspended=1`）。
5. **抓取部分失败丢数据**：成功期先落库再上抛（文案带 `upserted`/失败期）。
6. **E2E 打生产地雷（同 incident 038 族）**：`screening-page.spec.ts` API 基址硬编码 5173 会在隔离运行下打到 docker 生产栈——改为 config 派生，默认套件解析值逐字不变。

## 待人工验证项（owner 复核清单）

- [ ] `/screening` 事件观察区块实跑观感（三态徽章/证据引用/发起分析→队列徽标/来源过滤）
- [ ] **分诊质量抽检**：从清单抽 3-5 条人工核对 verdict 是否合理（LLM 分诊主观质量 = 人工项）
- [ ] 三个新 job（16:05/16:15/16:25）在真实调度上首跑观察（抓取源可用性、分诊批耗时、结算递延形态）
- [ ] N4 复查点：若 `event_triage.md` 模板系统性缺失，整批会被降级烧掉（当前仅 `degraded=N` 可观测）——事件线进入日常运行后建议加「全降级 fail-loud」护栏

## 已知边界与登记项（供最终评审分诊）

- N4 全降级 fail-loud 护栏（建议日常运行后复查）；N5 深回填（>4 年）日历缺口假设；N6 triage job 无 `snapshot_db_path` 形参（显式补跑入口不齐）。
- E2E 默认套件本地不可跑（端口被生产栈占用）——CI 兜底；ui-baseline 套件存在既有 CDN 字体 flake（与本次无因果，建议后续字体本地化）。
- 事件线 v1 不进执行链/不产订单（设计非目标）；记账人口=outperform（全候选反事实为非目标，如需另立 delta）。

## 结论

- [x] 机器门禁全部通过（含独立复跑与三轮证伪自检）
- [ ] 人工观感与分诊质量抽检待 owner 复核后随 archive 收口
