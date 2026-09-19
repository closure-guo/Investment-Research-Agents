# 030: 材料快照白名单漂移——compute 输出键丢 6/14，同一处第二次丢测量面（2026-09-17）

## 症状

A1（确定性指标注入）补测建单元时的零成本预检发现：`reports/ablation/p1/materials/` 下 20 个产物**全部**
缺 compute 输出键，共 115 个（每标的 5–6 个）：

| 缺键 | 说明 |
|---|---|
| `anomalies` / `traffic_lights` / `health_score` | 基本面分析师 context 直读 |
| `garp_result` | 基本面 context（估值策略结论） |
| `risk_metrics` | 技术面 context（含 beta / 波动率） |
| `price_levels` | A5（价位 sanity）消费面——**这一项是第二次出现** |

后果：A1 单元会全判 void（「快照缺 compute 输出键」）；A5 曾因此把「没测到」记成「ok」（§15/§16）。

## 根因

1. **白名单手抄漂移**：`product_from_state` 的 `snapshot_keys` 是一份人工维护的键名清单，
   与 `compute_metrics` 的输出集合没有任何约束关系。compute 侧写 14 个键，清单里只有 8 个；
   2026-09-17 的 A5 修复只往清单里补了 `price_levels` **一项**，其余 5 项继续丢。
2. **`snapshot_digest` 记占位串**：材料生成的 digest 回调默认值写死 `"injected"`
   （真实摘要函数 `_default_digest` 从未接线）→ 产物里的摘要不可核验，
   「三变体输入一致」的审计声明（消融 n=10 报告的口径基础）在这些材料上悬空。

## 影响面

- **消融侧**：任何以材料为输入的下游测量都缺面（A5 sanity、A1 无 compute 变体、族 B 的决策层材料）。
  两次踩到同类：`price_levels`（A5）→ 6 键（A1）。
- **生产侧**：不涉及——`compute_metrics` 与图通道契约（incident 027 已修）走的是 state 声明，
  与材料落盘无关。

## 判定

**真问题（测量设施的数据面缺陷）**，非模型能力问题。它同时是「指标低 ≠ 能力差」的反向实例：
缺面会把机制读成「无价值」（A5 的伪 ok、A1 的 void），先修观测装置再读数是本项目的既定纪律。

## 修复（已实施）

1. **排除式快照**：`_snapshot_keys(state)` = state 全部数据键 − 非数据键
   （`llm_config` / `api_key` / `callbacks` / `writer` / `query` / `focus` / `enable_web_search` /
   `analyst_reports` / `file_paths`）——新增 compute 输出自动进快照；
2. **完整性守卫**：与 `compute_metrics` 输出比对，缺一即 `ProductError`（显式拒绝落盘）；
   原始输入不全、compute 跑不动时打印警告并跳过校验（不静默、也不误伤）；
3. **回填既有产物**：`tests/scripts/backfill_materials_compute_outputs.py`（零 LLM、
   同一实现 `compute_metrics` 重算、只补缺键不覆盖已有值），产物内留痕 `snapshot_backfill`
   （补了哪些键 + 摘要前后）；
4. **摘要回真**：`run_materials` 的 digest 默认改 `_default_digest`，回填时补写真实摘要。

**验证**：20 产物回填 115 键后零缺键（`compute_output_keys(snapshot) - set(snapshot) == {}`）；
新增零 LLM 用例 `tests/evals/causal_ablation/test_pilot_cli.py::TestSnapshotKeys`（含「被排除项吃掉
compute 键必须报错」的反例）与 `TestMaterialsBackfill`（只补缺键 / 摘要留痕 / 入参不改）。

## 复现

```bash
uv run python tests/scripts/backfill_materials_compute_outputs.py --dry-run   # 报缺键，不写盘
uv run pytest tests/evals/causal_ablation/test_pilot_cli.py -q                # 守卫与回填用例
```
