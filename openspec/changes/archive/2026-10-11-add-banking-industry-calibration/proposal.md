# Proposal: add-banking-industry-calibration

## Why

issue #241（光大银行 601818 报告评估 P1-5）：指标模块与分析师 prompt 为纯制造业校准口径，对银行标的系统性失真——GARP 负债率 60% 硬编码使 91% 负债率的银行恒判不通过（死规则）；健康度 `INDUSTRY_OVERRIDES` 无金融条目，22.5 分被通用阈值压分并被辩论当空方核心论据引用 9 处；经营现金流/净利润倍数对银行不成立（OCF 含存贷款净进出）却在多空辩论被当有效筹码——错误前提被辩论共识化。

## What Changes

一期（全部确定性、零新数据源）：

1. **银行业阈值覆盖**（`INDUSTRY_OVERRIDES` += 银行/货币金融服务两键，共享同一覆盖表）：
   - **不适用排除**（override=None，新语义）：偿债维度全部（资产负债率/流动比率/速动比率/利息覆盖倍数/净债务÷EBITDA——银行以资本充足率为核心约束，存款是经营原料而非杠杆风险）、效率维度全部（总资产/存货/应收/应付周转率——资产即贷款，无经营循环）、OCF 衍生现金流信号（经营现金流÷净利润/FCF/现金流覆盖比率/FCF收益率/留存现金流比率——OCF 被存贷款污染）
   - **换银行业阈值**：ROE (13, 6, True)、ROA (0.9, 0.5, True)——校准锚点：光大 FY2025 实算（fixtures 银行利润表/资产负债表）+ 国有大行/股份行公开分布
2. **健康度维度剔除与满分缩放**：维度内全部指标被行业排除时该维度不计分，满分 = 25×适用维度数，rating 阈值按比例缩放（85%/60%）——通用路径满分恒 100 零回归
3. **GARP 银行业负债率豁免**：银行业标的负债率判「行业不适用」退出 failures，GARP 由其余三项判定；details 标注可审计
4. **快照增列信用减值损失**：`latest_period_snapshot` 利润表含「信用减值损失」列时增列金额（亿元）与同比——光大 -24% 的真实驱动（主动提储 vs 资产质量恶化）首次可溯源；列存在即纳入（制造业亦有此列，均受益）
5. **图表行业不适用标注**：银行业标的「毛利率与净利率」子图占位文本改为「银行业不适用毛利率口径（无营业成本概念）」——与 #240 的断线修复衔接，占位语义从「数据缺失」改为「行业不适用」
6. **分析师方法论现金流豁免指令**：fundamental_analyst.md 偿债/现金流两条方法论加银行业豁免句（OCF 倍数 MUST NOT 作为银行含金量论据）

**范围外（二期，另立 delta）**：银行专项四指标（NIM/不良率/拨备覆盖率/资本充足率）数据源接入——需实跑探测 akshare 银行专项接口可用性（东财域封禁环境），本 delta 不含；「全球市场份额」占位图移除——影响图表数量契约与 E2E 断言面，另行裁决。

## Impact

- Affected specs: `industry-threshold-coverage`（MODIFIED 覆盖机制 + ADDED 银行业覆盖）、`valuation-signal-integrity`（MODIFIED GARP 负债率）、`chart-data-integrity`（MODIFIED 利润率图占位）、`analyst-data-sources`（MODIFIED 快照增列）
- Affected code: `metrics/traffic_light.py`、`metrics/garp.py`、`nodes/compute.py`、`data/akshare_client.py`、`charts.py`、`nodes/report.py`（健康度满分渲染）、`prompts/fundamental_analyst.md`（合并后 deploy）
- 通用路径零回归：非银行业为 override 表新增键 + 维度满分恒 100 + 快照/图表按数据存在性变化
