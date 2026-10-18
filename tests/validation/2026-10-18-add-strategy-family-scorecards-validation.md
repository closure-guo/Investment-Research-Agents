# 人工验证报告: add-strategy-family-scorecards

**日期**: 2026-10-18
**验证人**: 自动化实施链（控制器 + 实施/评审 subagent 流水线）；**人工观感项待 owner 复核**（本报告 §待人工验证项）
**关联 delta**: openspec/changes/add-strategy-family-scorecards/
**E2E 门禁**: track-record 套件 `strategy-scorecard.spec.ts` 4/4 绿（实施者自跑 + 控制器独立复跑各一次；含证伪自检：篡改净期望期望值 → 红 → 改回 → 绿）；UI 基线重采后 `ui-baseline-pages` 14 passed

## 验证结果（机器可判项）

| 项 | 证据 | 通过 |
|---|---|---|
| 后端单元/端点测试 | `uv run pytest tests/outcome/test_scorecards.py tests/test_api_scorecards.py -q` → 23 passed | ✅ |
| 邻近回归 | outcome + execution + api_track_record → 874 passed（修复轮） | ✅ |
| 静态检查 | ruff 干净；新文件 mypy 零新增；前端 `tsc --noEmit` exit 0 | ✅ |
| 前端组件测试 | `npx vitest run src/test/trackRecord/` → 6 files / 76 passed | ✅ |
| E2E（track-record 套件） | `strategy-scorecard.spec.ts` 4/4；断言消费真实后端数字（毛/净/样本量/回撤/极端样本/容量原因/空态），零 mock 红线通过；每例前后复位 | ✅ |
| UI 像素基线 | track-record light/dark 重采（页面 1440×1046→1200，理由=新区块；重采两次逐字节一致）；其余 6 页 × 2 主题未动、容差未动 | ✅ |
| 规范校验 | `openspec validate add-strategy-family-scorecards --strict` 通过 | ✅ |
| 血缘评审 | 后端（含 F1-F4 修复复审）/前端/E2E 三轮任务评审全部 **Approved**，0 Critical；15 条 Minor 已登记 SDD 台账待最终评审分诊 | ✅ |

## 待人工验证项（owner 复核清单）

- [ ] `/track-record` 页面实跑观感：四槽卡片信息层级、族切换、极端样本明细表、口径注释可读性
- [ ] 主位确认：四槽为主指标、胜率仅为随行脚注
- [ ] （可选）抽 1 个族把页面读数与库内数据对账一次

## 异常记录

- 报告键名与 design 示例曾不一致（`settled_at/return` vs `date/ret`）：已由修复轮 F3 同步（design.md 以实现为准）。
- 基线采集发现滚动竞态（`App.tsx scrollToBottom` 以当时刻页高为滚动目标，与页面异步增高竞态；sticky 侧栏在成图中随 scrollY 等量位移）：已在用例中 `scrollTo` 钉到顶部态 + 断言 `scrollY===0`，属有意口径变更并留档。
- 一次更早采集的侧栏 ~0.59 匹配瞬态（疑入场动画类）未定因，留档；若复发按报告方向延长外壳等待。

## 结论

- [x] 机器门禁全部通过（含独立复跑取证）
- [ ] 人工观感项待 owner 复核后随 archive 收口
