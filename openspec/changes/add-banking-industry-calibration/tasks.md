# Tasks: add-banking-industry-calibration

## 1. 银行业阈值覆盖与排除语义（TDD 先红）

- [x] 1.1 失败测试：`is_banking_industry`（「银行」/「货币金融服务」/「国有大型银行」命中；「白酒」/None 不命中）
- [x] 1.2 失败测试：银行业 资产负债率 91% → 绝对灯/变化率灯/final 全 None（不适用排除），MUST NOT 红灯
- [x] 1.3 失败测试：银行业 ROE 9% → (13,6) 黄灯；ROA 0.61% → (0.9,0.5) 黄灯（通用阈值下 ROA 恒红）
- [x] 1.4 失败测试：cninfo「货币金融服务」命中同一覆盖表
- [x] 1.5 失败测试：非银行业全部指标沿用通用阈值（零回归）

## 2. 健康度维度剔除与满分缩放（TDD 先红）

- [x] 2.1 失败测试：银行业偿债+效率维度全排除 → score_cap=50、rating 阈值 42.5/30
- [x] 2.2 失败测试：数据缺失维度（非行业排除）不触发剔除（满分维持 100）
- [x] 2.3 失败测试：通用路径 score_cap=100、rating 阈值 85/60（零回归）
- [x] 2.4 失败测试：报告健康度行满分 <100 时呈现「满分 Y」，=100 维持现状

## 3. GARP 银行业负债率豁免（TDD 先红）

- [x] 3.1 失败测试：银行业负债率 0.91 → failures 无负债率项、details 标注 负债率_行业不适用=True 且保留数值
- [x] 3.2 失败测试：非银行业负债率 0.91 → 照旧「负债率 >= 60%」失败（零回归）
- [x] 3.3 失败测试：银行业负债率 NaN → 仍走缺失分桶（不适用语义不吞缺失）

## 4. 快照信用减值损失（TDD 先红）

- [x] 4.1 失败测试：银行利润表列在（208.79/159.02 亿）→ 快照含 信用减值损失=208.79 与同比≈31.3
- [x] 4.2 失败测试：列缺失或同期缺失 → 两项缺席、无 None 占位
- [x] 4.3 失败测试：报告披露节快照行追加「信用减值损失 X 亿（同比 Y%）」；字段缺席时零变化

## 5. 图表与 prompt（TDD 先红）

- [x] 5.1 失败测试：银行业毛利率全 None → 利润率子图占位含「银行业不适用毛利率口径」
- [x] 5.2 失败测试：非银行业 → 维持「利润率数据缺失」
- [x] 5.3 失败测试：fundamental_analyst.md 含金融业现金流豁免指令（OCF 倍数 MUST NOT 作为银行含金量论据）与偿债阈值豁免
- [x] 5.4 实现全部：traffic_light（覆盖表+排除+维度缩放+is_banking_industry）/ garp / compute（_try_garp industry）/ akshare_client（快照增列）/ charts（占位+industry 携带）/ report（满分渲染+快照行）/ fundamental_analyst.md

## 6. 验证与回归

- [x] 6.1 全量 pytest -m not live 0 失败；ruff/mypy 任务范围零错误
- [x] 6.2 openspec validate --strict 通过（四 spec）
- [x] 6.3 光大 601818 场景复核：fixtures 银行报表实算——GARP 不再恒败、健康度脱离通用压分（快照/健康度前后对照落 tests/validation 人工验证报告）
- [ ] 6.4 合并后 prompt deploy（fundamental_analyst.md）
- [ ] 6.5 issue #241 留评论：一期落地清单 + 二期（NIM/不良率/拨备覆盖率/资本充足率数据源探测）挂账
