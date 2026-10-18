# 人工验证报告: add-crowding-regime-positioning

**日期**: 2026-10-18
**验证人**: 自动化实施链（控制器 + 实施/评审 subagent 流水线）；**人工观感项待 owner 复核**（本报告 §待人工验证项）
**关联 delta**: openspec/changes/add-crowding-regime-positioning/
**E2E 门禁**: `market-regime.spec.ts` 3/3 绿（隔离 config 实跑 + 实施者证伪自检；默认套件端口 8000/5173 被本机生产栈占用，本地以 isolated config 代跑，**CI 兜底默认套件**）
**UI 基线**: 选股页 screening light/dark 重采（settle 钩子钉「温度计空态」；两次重采 sha256 逐字节一致；容差 0.001 未动；其余 6 页基线未动）

## 验证结果（机器可判项）

| 项 | 证据 | 通过 |
|---|---|---|
| 后端回归 | `uv run pytest tests/outcome/ tests/execution/ tests/test_api_regime.py tests/data/test_akshare_client.py -q` → **1044 passed** | ✅ |
| 静态检查 | `ruff check` 干净；`mypy src/finance_agent/outcome/regime/`（新包 7 文件）零问题 | ✅ |
| 前端 | `npx tsc --noEmit` exit 0；`npx vitest run` → **856 passed** | ✅ |
| E2E | `market-regime.spec.ts` 3/3（elevated / stale / 空态）+ 证伪自检（期望改错→红→改回→绿） | ✅ |
| 规范校验 | `openspec validate add-crowding-regime-positioning --strict` 通过 | ✅ |
| 评审链 | 后端（含 2 轮修复复审）/ 前端（含修复复审）/ E2E（含修复复审）**四轮全部 Approved**，0 Critical | ✅ |
| 关键实证① | **市值兜底链 live 端到端**：评审以「新浪列形 spot（无市值列）+ 真实腾讯兜底」实跑 `run_regime_daily` → 产行 `cap_source=tencent`、`small_cap_share=0.6` 手算一致——东财封禁环境下的失效模式实证消除 | ✅ |
| 关键实证② | 92xxxx 北交所号段修复：探针 5/5 码（含 920002/920008），金样本 fixture 走真实 GBK 字节 | ✅ |

## 修复历程（三轮真缺陷，全部闭环）

1. **市值源结构性阻断**（评审 Important → 实测确认）：本机 spot 走新浪回退、无流通市值列 → 日批恒 `cap_data_missing`。裁决：修订合同加「腾讯行情批量兜底链」（真实市值字段，非替代口径）+ `cap_source` 留痕；探针一次通过后实现并端到端验证。
2. **92xxxx 号段前缀错配**（修复复审 Important′）：`_to_sina_symbol` 把北交所新号段发成 `sh` → 兜底静默丢 351 只票 → 覆盖不全仍恒不产行；修复为 `bj`（沪 B `900xxx` 保持 `sh`），共享 helper 四类消费方逐点核过无回归。
3. **E2E 守卫空转**（Task 8 评审 Important）：15s 超时挂在无条件容器上；已移至三处数据依赖断言并证伪自检。

## 待人工验证项（owner 复核清单）

- [ ] `/screening` 页面实跑观感：温度计三态徽章/系数/四指标/理由行/陈旧披露
- [ ] 执行链摘要观察：`paper_execute_daily` 运行后 `job_runs.summary.regime` 出现状态与系数（`no_regime_data` 应在分位/数据就绪后消失）
- [ ] 分位爬坡期预期管理：上线后约 60 个交易日内分位为 null（温度计显示「分位样本积累中」），此前判定恒 normal（v1 设计）

## 已知边界与登记项（不阻塞归档，供最终评审/owner 分诊）

- `_add_prefix` 兄弟 helper 存在同族 92xxxx 缺口（报表抓取链，非 regime 链）——建议另立 issue。
- `cap_data_missing` 目前合并「单码缺失/源不可用」两义；腾讯兜底在封禁环境下为**常态路径**（约 56 批/日）——建议进数据源监控（incident 038 同族纪律）。
- seed `regime` 块形状错误（dict）会被守卫放行后静默跳过（Minors 在案）。
- `playwright.manual.config.ts` 会收集本 spec（既有同形先例；docker 栈未开 TESTING，只会 404 变红）。
- 前端遗留小项：pending 文案硬编码 60；types.ts 未含 `flat_count`/`cap_source`（白名单守卫忽略多余键，功能无碍）。

## 结论

- [x] 机器门禁全部通过（含独立复跑与 live 端到端实证）
- [ ] 人工观感项待 owner 复核后随 archive 收口
