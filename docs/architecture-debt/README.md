# 架构欠账档案（白话版）

> 2026-11-21 架构评审产出的 6 个候选，经拍板**全部立项**。每份档案用白话写：毛病是什么、怎么发现的、打算怎么修、牵涉哪些文件、有什么坑。
>
> 行话版报告：临时目录 `architecture-review-20261121-113313.html`（临时文件，可能被清理；所有证据已抄进各档案）。
> 侦察方法：git log 近 60 commits 定热点 → 三个独立侦察通道 → openspec/ADR 核对。

## 档案清单

| 编号 | 一句话 | 白话档案 | 强度 |
|---|---|---|---|
| C1 | 查价格的程序像打地鼠，7 周打了 12 次补丁 | [c1-price-check.md](c1-price-check.md) | Strong |
| C2 | 同一个「卖出」，四段代码翻译出四个意思，战绩方向已反（#606） | [c2-price-direction.md](c2-price-direction.md) | Strong |
| C3 | 算战绩的股价有两套，三家各记各的，一家压根没管 | [c3-settlement-prices.md](c3-settlement-prices.md) | Strong |
| C4 | 连数据库的规程抄了 22 份，其中 5 份一字不差 | [c4-sqlite-connector.md](c4-sqlite-connector.md) | Strong |
| C5 | 分析任务的「进行中/完成/失败」规则写在 4 个地方——一盏灯四个开关 | [c5-session-status.md](c5-session-status.md) | Strong |
| C6 | 三个小件：规则抄 5 份 / 一段死代码 / 577 行造数函数住错地方 | [c6-small-items.md](c6-small-items.md) | Worth exploring |

## 执行顺序（已定）

| 批次 | 内容 | 为什么这么排 | 管线要求 |
|---|---|---|---|
| 第 1 批 | **C2 + C4 并行** | C2 是唯一「会算错钱」的（记分卡方向反）；C4 纯结构零风险，互不相干可搭车 | C2 走 OpenSpec delta（契约真空需新增条目）；C4 无行为变更，正常 PR + 全量测试 |
| 第 2 批 | **C6 三小件** | 全是低成本清理，可与第 1 批同车或紧随 | 删死代码前先核 evals/golden 用法；test_seed 搬家同步 E2E |
| 第 3 批 | **C3 结算拿价入口** | 消灭口径拿错事故族，#607 的 fail-closed 逻辑要原样收编 | OpenSpec delta（结算口径契约） |
| 第 4 批 | **C5 会话状态归一** | 涉及会话状态流转 = 交互类变更 | 按仓库红线走完整管线：delta + E2E 门禁 + 人工验证报告 |
| 第 5 批 | **C1 价格归类器** | 最持续的维护烧钱点，但要先把 spec 的豁免条目从 token 粒度换成归类粒度 | OpenSpec delta（改 price-level-tooling 契约粒度） |

## 每批的共同验收

- TDD：先写失败测试再修（仓库红线）
- 全量 `uv run pytest` + `ruff` + `mypy`；注意本机 exe 路径限制，测试在仓库外根 python 跑
- 交互类（第 4 批）另加 E2E 门禁 + 人工验证报告落 `tests/validation/`
- 部署遵守红线窗口（交易日 15:30–17:10 / 17:55–20:45 禁重建），重建前查 `/api/sessions` 与 `/api/v1/ops/jobs`
