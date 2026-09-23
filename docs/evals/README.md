# 评估结论注册表

| 报告 | 状态 | 取代者 |
|---|---|---|
| docs/evals/2026-09-02-评估体系开机与coverage-v3记录.md | ![active](badge:active) | — |
| docs/evals/2026-09-03-消融n10权威结果.md | ![active](badge:active) | — |
| docs/evals/2026-09-10-judge校准round5报告.md | ![active](badge:active) | — |
| docs/evals/2026-09-11-citation门禁整改r4收口报告.md | ![active](badge:active) | — |
| docs/evals/2026-09-11-judge校准复盘-round5到r3问题发现链.md | ![active](badge:active) | — |
| docs/evals/2026-09-12-decision-semantics-r5重评收口报告.md | ![active](badge:active) | — |
| docs/evals/2026-09-13-round7-judge校准报告.md | ![active](badge:active) | — |
| docs/evals/2026-09-13-round8-维护者代裁报告.md | ![active](badge:active) | — |
| docs/evals/2026-09-14-round9-v8审计.md | ![active](badge:active) | — |
| docs/evals/2026-09-21-决策层全watch取证.md | ![active](badge:active) | — |
| evals/ablation/results/pilot.md | ![superseded](badge:superseded) → docs/evals/2026-09-03-消融n10权威结果.md | docs/evals/2026-09-03-消融n10权威结果.md |
| evals/backtest/results/pilot-2023-shock.md | ![active](badge:active) | — |

## 未标注生命周期（4 份）

- docs/evals/dataset-baseline.md
- docs/evals/hosted-evaluator-template.md
- docs/evals/metrics.md
- docs/evals/金融分析Agent-GoldenSet设计文档.md


## 索引维护

- **生成方式**：`evals/causal_ablation/status_index.py` 的 `collect_status_index`（收集）+ `render_status_index`（渲染）。纯读扫描 + 纯渲染，不改动任何报告；无时间戳，输出确定性。报告状态由撰写者维护，本索引只负责让它可见。
- **落位说明**：`status_index.py` / 规范均未固定索引的输出路径，本索引按约定落 `docs/evals/README.md`（口径变更见 `docs/evals/metrics.md` §1.7④ 与时间线「消融 v2 口径切点」段）。
- **扫描范围**：`docs/evals/*.md`（非递归）+ `evals/ablation/results/*.md` + `evals/backtest/results/*.md`；本文件（索引自身）不参与扫描，避免索引自登。
- **状态契约覆盖面**：`evals/ablation/results/*.md` 与 `evals/backtest/results/*.md` 已是强制契约（`openspec/specs/evaluation`、`tests/evals/test_report_status.py` 参数化扫描）；本 delta 把该字段扩为 evals 实验报告通则（`openspec/changes/revamp-ablation-v2-causal-claims`，待 sync），存量 `docs/evals/*.md` 报告尚未回填，故整体落在「未标注生命周期」段——**未标注 = 未登记，不等于结论作废**（台账 `metrics.md`、模板、设计文档等同列，属预期）。
- **刷新命令**（仓库根目录执行，输出替换上方标记区）：

```bash
uv run python - <<'PY'
from pathlib import Path

from evals.causal_ablation.status_index import collect_status_index, render_status_index

entries, unstamped = [], []
for d in ("docs/evals", "evals/ablation/results", "evals/backtest/results"):
    e, u = collect_status_index(Path(d))
    entries += e
    unstamped += [p for p in u if Path(p).name != "README.md"]  # 索引自身不登记
print(render_status_index(entries, unstamped), end="")
PY
```

- **登记方式**：报告头部加生命周期字段（`status`：`active`；结论被推翻时改 `superseded-by: <取代者路径>` + 就地撤回说明，指向路径须真实存在）——字面格式与校验以 `evals/causal_ablation/report_status.py`、`tests/evals/causal_ablation/test_report_status.py` 为准。
- **渲染器已知形态（冻结代码，本索引如实渲染、未手工改写）**：① 「取代者」列只在 status 头**同一行**捕到路径时才有值，`active` 行读作 `—`（G7/⑤a 修复了此前跨行捕获、把下一行首个 token 当 target 的缺陷）；② `superseded` 行的状态徽章带真实取代者路径（`status_badge(status, target)`，与同行「取代者」列一致；G7/⑤b 修复了此前恒显 `<缺目标>` 的缺陷）；③ 徽章 `badge:` 为占位图片地址，渲染时按 alt 文本读即可。
- **注意（扫描器判据是字面标记）**：`docs/evals/` 下的非报告文档（含本文件、`metrics.md`、模板）不得复现报告头的字面标记，否则会被误当作报告——取值合法会被显示为 `active`，取值非法会让收集器直接抛错。本文件与 `metrics.md` 因该原因以不带粗体星号的写法描述字段名。
