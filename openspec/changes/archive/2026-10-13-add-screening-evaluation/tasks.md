# Tasks: add-screening-evaluation

> 回填（2026-10-13）：T1–T3 已实施（提交 `a344683d`/`4198ef42`/`b2f61ce5`），T4 收口见
> `tests/validation/2026-10-13-add-screening-evaluation-validation.md` 与合成样本报告
> `docs/evals/2026-10-13-screening-forward-sample.md`。

## 1. 前向跟踪
- [x] 1.1 候选池 5/10/20 日前向收益自动跟踪（复用 outcome 日批）+ 时点截断单测
  - `evals/screening/forward.py`：入选组（selection_pool）vs 落选组（l2_candidates 非池）逐日分组，
    入场走 `match_order`（D+1 开盘 ± 滑点；一字涨停不成交 → 剔除 + `unfilled_counts` 计数），
    出场 = horizon 日收盘；只用 ≤ as_of 数据。单测含手算小面板逐数对照 + 未成交剔除 + 时点截断。
  - 说明：日批自动跟踪的**调度接线**按计划落在后续 cadence（见 4.2），本 change 交付可调用的跟踪函数与报告。

## 2. 消融变体
- [x] 2.1 三变体（因子打分/固定种子随机/等权指数）配对 bootstrap + 种子可复现单测
  - `evals/screening/ablation.py`：`random_selection(seed=42)` 确定性（spec 场景：两次执行序列完全一致）、
    等权指数基线、`paired_bootstrap_ci(B=10000, seed=42)`；CI 含 0 → 报告仅「无显著差异」（措辞守卫单测）。
- [x] 2.2 「±L3 粗筛」变体（keep/drop 组前向收益与踩雷率对照）
  - `evals/screening/l3.py`：keep/drop 两组 n/均值/胜率/踩雷率（`blowup_threshold=-0.1`）+ 配对 CI；
    spec 场景（keep +2.1% / drop −3.4% / CI 不含 0 → 「具有排雷价值」+ 窗口与 n 披露）与反向（含 0 → 无显著差异）均单测钉死。

## 3. 因子分层考核
- [x] 3.1 LLM 因子 vs builtin 因子分层前向统计登记进 evals 报告
  - `evals/screening/stratify.py`：builtin-only / LLM-only 因子子集重跑打分 → 两分层 Top-N 前向对照 + 配对 CI；
    报告渲染含两分层行与有效 n/口径披露。

## 4. 验证
- [x] 4.1 全量测试通过
  - `uv run pytest tests/evals/screening -q` → 35 passed；端到端合成跑批 11/11 checks PASS（`-e2e.json`）；
    全量（not live）结果见验证报告（既有 llm env flake 归因不变）。
- [x] 4.2 每 20 交易日前向报告落 `docs/evals/`，口径含有效 n 与 CI 披露
  - **样本报告已落** `docs/evals/2026-10-13-screening-forward-sample.md`（**合成数据**，明示）。
    live cadence（每 20 交易日真实数据报告）**受阻/后续项**：本机东财 IP 封禁（issue #325）+ 选股上线后
    需先积累 20 个交易日快照 → 挂 issue，不伪造真实数据报告。
- [x] 4.3 既有测试无修改通过（红线）
  - 全部为新增文件（`evals/screening/` 包 + `tests/evals/screening/`），既有测试零修改。
