# Delta for citation-verification

## MODIFIED Requirements

### Requirement: 计算型声明重算注册表全覆盖

claim 的**校验深度 SHALL NOT 由其自我声明（`claim_type`）决定**：`field_ref` 根键命中重算注册表的
claim（无论申报为 `numerical` 还是 `computational`）SHALL 一律从 state 原始数据重算 ground-truth；
重算注册表 SHALL 覆盖 `metrics/` 模块的全部纯函数指标族（偿债、盈利、运营、现金流、杜邦、技术指标、
风控指标）**以及全部由代码从数据快照计算的派生字段（含 `garp_result`、`anomalies`）**，每个注册根键
SHALL 有独立的重算 fixture 测试（从原始报表数据重算，不依赖 LLM、不调外部接口）。字符串枚举型派生
字段（如 GARP failures 集合）SHALL 按集合相等比对。未注册根键的计算型 claim SHALL 判 UNVERIFIABLE，
且 SHALL 计入覆盖缺口指标供覆盖率审计。重算与直读两条路径 SHALL 共用同一比对实现（方向对齐 +
percent/万/亿候选归一 + 相对容差），SHALL NOT 各自维护容差语义。重算输入不可得（如 state 缺原始报表）
时 SHALL 显式降级回直读比对并保留覆盖缺口标记，SHALL NOT 静默等价于未校验。

(Previously: 重算路由由 `claim_type=computational` 触发——LLM 把自算值标成 numerical 即可绕过重算，P1 冻结批 5/5 复现)

#### Scenario: 已注册指标重算通过

- **GIVEN** Agent 报告含 claim（如 `solvency_metrics.资产负债率.2024`），其根键已注册
- **WHEN** 执行校验
- **THEN** 系统 SHALL 从 state 原始数据经对应纯函数重算 ground-truth，按相对容差 0.5% 判定 PASS/FAIL

#### Scenario: 标签不得绕过重算

- **GIVEN** claim 申报 `claim_type="numerical"` 而 `field_ref` 根键已注册重算
- **WHEN** 该根键的派生 dict 与原始数据不一致（污染或自算错误）
- **THEN** 校验 SHALL 判 FAIL（真值取自重算，SHALL NOT 取自可被污染的派生 dict）

#### Scenario: 未注册根键显式降级

- **WHEN** 计算型 claim 的根键未在注册表中
- **THEN** 校验结果 SHALL 为 UNVERIFIABLE
- **AND** 该事件 SHALL 计入覆盖缺口计数，SHALL NOT 静默等同于 FAIL 或被忽略

#### Scenario: 容差语义不回归

- **WHEN** 注册表扩展或路由变更后执行任意校验
- **THEN** 数值容差（绝对 0.01 / 相对 0.5%）与三态裁决（PASS/FAIL/UNVERIFIABLE）语义 SHALL 与既有契约一致

#### Scenario: 快照派生字段可验

- **WHEN** Agent 报告含 `garp_result`、`anomalies` 等快照派生字段的 claim
- **THEN** 校验 SHALL 经注册的重算/集合比对得出 PASS/FAIL，SHALL NOT 恒为 UNVERIFIABLE
