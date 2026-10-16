# 闸门验证三颗钉子报告（2026-10-17）

- 背景：因子库空库 → L2 拒打分 → 选股批次 failed；「闸门能不能保证正确」的核验需求。
  先行实证（2026-10-16 晚）：IC 实现与独立 oracle 互证 1e-16；三条拒收（BIAS20/量比对/
  ClosePosition）在准入面板上精确复现——拒收是真实读数而非闸门抽风。
- 本报告 = 三颗钉子的实测读数：**注入标定（钉子 1）· 定向变异探针（钉子 2）· 回归锚点
  （钉子 3）**。结论：闸门从「自证」升级为「互证 + 实测标定 + 防漂移」——仍不能
  「保证」（统计闸门无保证一说），但已知边界被进一步收窄为实测数字。

## 钉子 1：真实面板注入标定（功率曲线 + H0 通过率）

方法：150 日真实准入面板上把 `volume` 帧替换为「次日远期收益横截面秩 + σ×噪声」，
经真实 `admit_candidates`（每轮独立临时库，防闸 0 交叉）过五道闸。
脚本 `tests/scripts/gate_calibration_injection.py`，产物 `reports/gate-calibration-20261017.json`。

| 噪声 σ | 实测 ICIR（3 种子） | 裁决 |
|---|---|---|
| 2 | 9.6 – 10.7 | 全部入库 |
| 4 | 4.8 – 5.3 | 全部入库 |
| 8 | 2.4 – 2.6 | 全部入库 |
| 16 | 1.19 – 1.30 | 全部入库 |
| 32 | 0.59 – 0.65 | 全部入库 |
| 64 | 0.29 – 0.35 | **拒：闸⑤ OOS ×2 / 闸② ×1** |
| 256 | 0.06 – 0.12 | 拒：闸② WEAK_ICIR ×3 |
| H0（纯噪声，20 次） | — | **0/20 误纳** |

**解读**：① 本装置下准入边界落在 **ICIR ≈0.35–0.59 之间**——闸②的 0.3 是必要不充分，
**闸⑤（样本外复核）在近边界处是主约束**（ICIR 0.29–0.35 三例中两例倒在 OOS）；② 纯噪声
20 次零误纳，多重检验机器（置换 + BH-FDR + OOS）工作正常；③ 「0.3」由此从文档背书变为
**实测标定**：在单窗口装置里，达标线附近的因子能否入库更多取决于 OOS 段表现而非全样本 ICIR。

**局限（如实）**：注入因子是理想化的（无缺失、平稳、单窗口）；真实因子面临 regime 漂移，
其假阴性率高于本装置；标定量化的是**机器特性**，不是任何具体因子的命运。

## 钉子 2：定向变异探针（测试杀伤率）

方法：对闸门机器人工策展 **14 条定点变异**（一行级语义篡改：min_periods / shift / horizon /
阈值量级 / 边界翻转 / 公式丢项 / 排序方向 / 条件恒假 / 校验去除），逐条注入 → 运行聚焦
测试子集 → 恢复。脚本 `tests/scripts/mutation_probe.py`，产物 `reports/mutation-probe-20261017.json`。

- **首轮：11/14 杀死**；1 存活 = `ranker-scorable-boundary`（降级谓词 `>=`→`>`，Σ|IC| 恰等于
  阈值处无测试钉边界）——**真实测试盲区**；2 条锚点未命中（变异集锚点与源码不符，已修正）。
- **补钉后复跑：14/14 杀死，零存活**。盲区处置 = 新增
  `test_has_scorable_factors_boundary_pinned`（恰等阈值 → 可打分；与 `factor_weights` 的
  `<` 拒绝对偶）。

**解读**：闸门机器关键路径的测试**有真实杀伤力**（非「全绿即通过」）；「测试盲区清单」以
工具化方式可再生成（变异集即审计对象；本探针非穷举，属策展集）。

## 钉子 3：回归锚点（冻结真实面板子集）

方法：从真实准入面板确定性抽取 **200 票 × 150 日 × 六字段**（按代码升序）冻结为
`tests/fixtures/gate_anchor_panel.npz`（354 KB，float32；锚点在冻结值上自洽计算），
5 条锚点表达式的 IC/ICIR 读数落 `gate_anchor_expected.json`；测试
`tests/factors/test_gate_anchors.py`（rel=1e-6）在**冻结数据**上复现读数——闸门机器
（面板→求值→IC）任何重构都必须复现，捕捉静默漂移。

锚点读数（h=1，子集口径）：

| 表达式 | IC mean | ICIR |
|---|---|---|
| `div(close,ts_mean(close,20))` | −0.019671 | −0.099410 |
| `sub(div(close,ts_mean(close,20)),1)` | −0.019671 | −0.099410 |
| `delta(close,5)` | 0.003431 | 0.019995 |
| `div(volume,ts_mean(volume,20))` | −0.040408 | −0.296532 |
| `ts_std(close,20)` | −0.032410 | −0.170217 |

**局限**：子集读数 ≠ 生产全市场读数；锚点是内部一致性回归（防漂移），不是生产口径承诺。
生成器 `tests/scripts/make_gate_anchor_fixture.py`（数据版本演进后按需重生成并更新锚点）。

## 复现命令

```bash
uv run python tests/scripts/gate_calibration_injection.py --out reports/gate-calibration-20261017.json
uv run python tests/scripts/mutation_probe.py --out reports/mutation-probe-20261017.json
uv run python tests/scripts/make_gate_anchor_fixture.py      # 重生成夹具（如需）
uv run pytest tests/factors/test_gate_anchors.py -q          # 锚点回归（默认套件内）
```

## 遗留

- 钉子 1 延伸：多窗口/真实因子族标定（把「单窗噪声」的假阴性率也量化）；
- **跨批次 FDR 全局账本**（结构性缺口，已立 issue #355）；
- 变异探针的变异集扩充（按模块轮换策展）。
