# 决策买入闸门修复 · 合成回归与发布报告（2026-11-07）

> Delta：`openspec/changes/update-decision-buy-gates/`（validate --strict 通过）｜计划：`docs/superpowers/plans/2026-11-07-decision-buy-gates.md`
> 分支 `feat/decision-buy-gates-20261107`（堆叠于 feat/zero-buy-round2-p0 @ 1206d149）：8f8b2060 delta+计划 → 40b281b1 T1 → 43e51f67 T2 → 59e91d9 T3 → 契约钉+锚定澄清 → 本报告

## 1. 回归判据落点（预注册）

运行方式：`scripts/run_synthetic_case.py`（**不带 --variant** = 新生产提示词；清空 LANGFUSE_* 键强制本地加载，发布前验证；顺序执行防并发限流）。

| 案例 | 预注册判据 | 首轮 | 修复后 | 判定 |
|---|---|---|---|---|
| perfect_cooled（v4） | **必须 buy** | **buy**（0.5/light，stop 16.85=2.4σ 锚定生效） | — | ✅ |
| perfect_tight（v5） | **必须 buy**（fallback：watch+显式缺口） | watch + **显式「可执行参数缺口」**申报 | **buy**（rerun1） | ✅（经 1 次定向修复循环） |
| perfect（v3.1 超买） | buy 或 watch，禁引用催化剂/均衡/止损理由 | **buy**（0.52/light，stop 17.11≈2.2σ） | — | ✅ |
| defect | **必须 sell** | **sell**（0.62，exit 节奏完整） | — | ✅ |

**修复循环**（tight 首轮 watch+缺口 → PARTIAL）：根因是 trader 用「参考带宽相对考核期波动」的变体框架绕开锚定规则自行 watch——契约遵循缺口而非契约缺口。修复 = trader.md 锚定段补「预算化即定价」条（锚定止损即定价，变体顾虑只能降仓位档或留待裁判申报缺口，不构成 trader 侧 watch 独立理由）+ 契约测试钉住。重跑 1 次即 buy。

## 2. 行为证据（新契约被执行，不只是没拦）

- **止损锚定照算**：cooled 轮 trader reasoning 逐步演算「日波动=30.36%/√252≈1.91%，带上沿 ≈1.27×日波动属噪声内止损，下移至 ≥2×日波动：stop=16.85」。
- **否决纪律落地**：perfect（超买）轮 risk_judge 原话「保守/中性方的反对集中在止损宽度、名义赔率、数据盲区与窗口错配，**按否决纪律均属执行参数与置信度问题，不构成方向否决**，故不下调为 watch」。
- **方向保留、仓位消化**：tight rerun 轮 trader 提 moderate，裁判降档 light——方向无否决。
- **协商回路激活**：cooled 轮 FM 以「赔率未过 2:1 门槛」退回一次，trader 补价位后过闸；四案例 reason 全程零禁用理由命中（催化剂/证据均衡/止损窄于噪声）。
- **置信度去锚**：五次运行 confidence 分布 0.45–0.62（旧生产恒扎堆 0.50–0.55 示例锚）。
- **均衡带收窄生效**：tight 首轮「证据均衡带：是」仅作对峙点披露，无「无增量默认观望」降级。

## 3. 发布与回滚

- `scripts/deploy_prompts.py`：**导入 7 / 跳过 14 / 失败 0**（trader、risk_judge、fund_manager、4 分析师——恰为本变更全部改动面）。
- 生产版本（容器内 loader 实探）：**trader v38 / risk_judge v36 / fund_manager v33**（各 +1），新文本「结算口径」确认在位；TTL 缓存 30s 内生效，无需重启。
- **回滚路径**：`git revert` 对应提示词提交 → 重跑 deploy_prompts.py（Langfuse 版本化，无状态迁移；回滚窗口内无运行中分析）。
- 生产库完整性：`data/sessions.db` predictions=287 / sessions=586 逐字不变。

## 4. 验证与已知问题

- 契约测试 126 + 执行/节点面 399 全绿；ruff 全绿；**mypy 98 错误均为基线预存**（state.py 重复键等来自 #559 及更早，本分支 0 新增）；全量 pytest 1581 passed + 1 失败为 `test_fm_decision_live` @live 历史数据问题（命中 2026-09-25 旧 trace，早于 reasoning 强制契约，与本分支无关）。
- **已知问题 1（措辞歧义）**：tight rerun 的最终 stop=17.2229（带下沿 ≈1.29σ）——「max(2×日波动, 最近结构位)」中结构位腿被读成价格 max（越宽越安全语义被读反）。本轮不改文本（保持回归=发布逐字一致），挂账为下一轮措辞修正：「止损距离 = max(2×日波动, 至最近结构位的距离)」+ 届时单案例迷你回归。
- **已知问题 2**：citation 覆盖 0.667–0.857、`citation_blocked=True`（auto-retry 关闭，只告警不改写动作）——沿承 P0 报告披露口径，不读作 citation 全绿。
- **已知问题 3（ops 挂账沿承）**：宿主侧 prompts REST 拉取 400/404（loader 回退本地），容器内正常——生产不受影响；round-1 已建议单独 issue 排查。

## 5. 成本

回归 5 次运行（4+1 rerun）+ 子代理实施/审查，其中运行侧无 Langfuse 计量（本地模式）；按同配置单次 ≈10 万 tokens 估算，运行侧 ≈50 万以内（预算闸 60 万，未触发）。

## 6. 后继

1. delta tasks.md 全勾 → 走 sync + archive（E2E 不适用：纯后端提示词/逻辑变更，无交互面改动）。
2. 已知问题 1 的措辞修正与迷你回归；四态 schema（INSUFFICIENT_DATA）另立 delta。
3. P4 真实票真空期验收（10 只事后超额>2%）——生产提示词已切换，本周期的日报/日批即为首批真实观察面；连续零买入告警（#569 观测层）继续值守。
