# add-banking-industry-calibration delta — industry-threshold-coverage

## MODIFIED Requirements

### Requirement: 行业阈值覆盖机制

红黄绿灯阈值 SHALL 支持「行业覆盖优先于通用阈值」：以行业名称子串模糊匹配 `INDUSTRY_OVERRIDES`（现有白酒/酿酒/半导体设备条目为此机制既有实例），命中的指标用行业阈值评灯，未命中的指标沿用通用阈值。新增行业覆盖条目 SHALL 附校准依据（同行业代表公司指标分布的量化说明），MUST NOT 拍脑袋取值。

覆盖值 SHALL 支持第三种语义「行业不适用排除」（override 值为 None）：被排除指标 MUST NOT 参与绝对值评灯、变化率评灯与安全地板（final 恒 None，与数据缺失在评分中同样跳过），行业口径标注 SHALL 呈现该排除——排除用于「指标对行业无判别意义」（如银行业资产负债率：存款是经营原料而非杠杆风险，风险约束是资本充足率）的形态，MUST NOT 以无校准依据的臆造阈值替代排除。

#### Scenario: 命中行业覆盖的指标改用行业阈值

- GIVEN 个股行业名称含「半导体设备」且覆盖表含该键的存货周转率阈值
- WHEN 红黄绿灯评判存货周转率
- THEN SHALL 用半导体设备阈值评灯，MUST NOT 用通用阈值 (5, 2)

#### Scenario: 未命中指标沿用通用阈值

- GIVEN 个股行业名称含「半导体设备」但覆盖表无 ROE 条目
- WHEN 评判 ROE
- THEN SHALL 用通用 ROE 阈值评灯

#### Scenario: 行业不适用指标不参与评灯

- GIVEN 个股行业名称含「银行」且覆盖表该键 资产负债率 值为 None
- WHEN 红黄绿灯评判资产负债率（值 91%）
- THEN 绝对值灯/变化率灯/final SHALL 全为 None，MUST NOT 用通用阈值 (40, 65) 评红灯
- AND 行业口径标注 SHALL 含 资产负债率（不适用）

#### Scenario: 部分排除时维度内其余指标照常评灯

- GIVEN 银行业覆盖排除毛利率但保留 ROE 阈值覆盖
- WHEN 红黄绿灯评判盈利维度
- THEN 毛利率 SHALL 为 None，ROE SHALL 按 (13, 6) 评灯

### Requirement: 半导体设备行业首批覆盖

系统 SHALL 为半导体设备行业（行业名称子串「半导体设备」）提供首批阈值覆盖，至少覆盖：存货周转率、速动比率、应付账款周转率。覆盖依据：设备商收入按客户验收确认、存货中大比例为已发货待验收设备、负债中合同负债（客户预付款）占比高，通用制造业阈值系统性误判为红灯。具体取值见 change design.md 的校准论证。

#### Scenario: 拓荆科技存货周转率不再误判红灯

- GIVEN 拓荆科技(688072) 行业为半导体设备、年报口径存货周转率 0.56
- WHEN 红黄绿灯按行业覆盖阈值评判
- THEN 存货周转率 SHALL NOT 评红灯（按 design.md 校准值落在绿或黄区间）

#### Scenario: 健康度评分随覆盖阈值重算

- GIVEN 同一股票在行业覆盖前后各评一次健康度
- WHEN 覆盖生效
- THEN 健康度评分 SHALL 按覆盖后灯色重算
- AND 评分变化可由阈值覆盖解释（不引入其他变量）

### Requirement: 健康度评分行业口径披露

当行业阈值覆盖生效于任一评灯指标时，红黄绿灯与健康度评分输出 SHALL 携带 `industry_override` 标注（行业名 + 覆盖指标清单），报告渲染层 SHALL 使读者可见评分所用口径；无覆盖时输出 SHALL 标注通用口径。

维度内全部指标被行业排除时，该维度 SHALL 从健康度评分中剔除：总分满分 = 25 × 适用维度数（结果携带 `score_cap`），rating 阈值按满分等比缩放（healthy ≥ 0.85×满分、caution ≥ 0.60×满分）。数据缺失导致的维度零分 MUST NOT 触发剔除（缺失是诚实信号，行业不适用才是剔除条件）。通用路径适用维度数恒为 4、满分恒 100、rating 阈值恒 85/60（零回归）。报告健康度行 SHALL 在满分低于 100 时呈现满分（如「X 分（rating，满分 Y）」），满分 100 时维持现状形态。

#### Scenario: 覆盖生效时评分携带口径标注

- GIVEN 拓荆科技健康度评分计算完成且存货周转率经行业覆盖评灯
- WHEN 评分结果写入 state
- THEN 结果 SHALL 含 `industry_override`（行业「半导体设备」、覆盖指标含存货周转率）
- AND 报告健康度章节 SHALL 展示该口径标注

#### Scenario: 无覆盖时标注通用口径

- GIVEN 贵州茅台行业为白酒、存货周转率命中白酒覆盖
- WHEN 健康度评分输出
- THEN `industry_override` SHALL 记录白酒覆盖（现有行为显式化）

#### Scenario: 银行业维度剔除与满分缩放

- GIVEN 银行业标的偿债与效率两维度全部指标被行业排除、盈利与现金流两维度正常评灯
- WHEN 健康度评分计算
- THEN 总分满分 SHALL 为 50（`score_cap`=50），rating 阈值 SHALL 为 42.5/30
- AND 报告健康度行 SHALL 呈现「满分 50」

#### Scenario: 数据缺失维度不触发剔除

- GIVEN 非银行业标的某维度因数据缺失 count=0
- WHEN 健康度评分计算
- THEN 该维度 SHALL 记 0 分且满分维持 100，MUST NOT 触发剔除

## ADDED Requirements

### Requirement: 银行业口径覆盖

系统 SHALL 为银行业（行业名称子串「银行」，及 cninfo 降级源形态「货币金融服务」）提供阈值覆盖，共享同一覆盖表。**不适用排除**：偿债维度全部（资产负债率/流动比率/速动比率/利息覆盖倍数/净债务÷EBITDA——银行以资本充足率为核心约束，专项指标接入为后续变更）、效率维度全部（总资产/存货/应收/应付周转率——银行资产即贷款与投资，无制造业经营循环）、OCF 衍生现金流信号（经营现金流÷净利润/FCF/现金流覆盖比率/FCF收益率/留存现金流比率——银行 OCF 含存贷款净进出，倍数信号不成立）。**换银行业阈值**：ROE (13, 6, True)、ROA (0.9, 0.5, True)。校准依据见 change design.md D4（光大 FY2025 实算锚点 + 国有大行/股份行公开分布）。

分析师 prompt 方法论 SHALL 携带银行业豁免指令：金融类标的的经营现金流÷净利润倍数与 FCF 含金量信号不适用，MUST NOT 作为银行标的多空论据；偿债参考阈值（负债率 60% 等）对银行业不适用。

#### Scenario: 银行业标的健康度不再被通用阈值压分

- GIVEN 光大银行(601818) 行业为银行、负债率 91%（通用阈值 (40, 65) 下为红灯）
- WHEN 红黄绿灯与健康度按银行业覆盖评判
- THEN 资产负债率 SHALL 不参与评灯与评分，MUST NOT 产出红灯
- AND ROE 9.0% SHALL 按 (13, 6) 评黄灯（通用阈值 (15, 8) 下同为黄，ROA 按银行业阈值脱离红灯）

#### Scenario: OCF 倍数信号对银行禁用

- GIVEN 银行业标的经营现金流/净利润 = 4.16（含存贷款净进出的污染值）
- WHEN 红黄绿灯评判
- THEN 该指标 SHALL 不适用（final=None），MUST NOT 产出绿灯被引用为「利润含金量高」
- AND 分析师 prompt SHALL 含金融业现金流豁免指令

#### Scenario: cninfo 行业形态命中同一覆盖

- GIVEN fetch_industry 降级至 cninfo、行业为「货币金融服务」
- WHEN 红黄绿灯评判
- THEN SHALL 命中银行业覆盖（与「银行」子串同一覆盖表）
