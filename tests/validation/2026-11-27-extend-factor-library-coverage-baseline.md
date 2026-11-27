# P1 基线冻结：extend-factor-library-coverage

**用途**：delta `extend-factor-library-coverage` P1（算子与计数口径）实施前的事实基线，供重放对比与回归核对。全部只读取数，未触碰任何 SQLite（宿主红线）。

**取数时刻**：2026-11-27 23:4x（Asia/Shanghai）；来源：`GET /api/factors`、`GET /api/factor-research/harvest`、`GET /api/screening/batches`、容器内只读 `data/screening.db`（l2_candidates）与 `data/factor_research/harvest_state.json`（mtime 2026-10-30，其后未更新）。

## 1. 因子库实态（`[实测]`）

| id | status | category | source | ic_mean_60d | icir_60d | pool_increment | created | expression |
|---|---|---|---|---|---|---|---|---|
| 1 | active | 外部 | external | -0.0327 | -0.3898 | +0.3584 | 2026-10-19 | `sub(div(volume,ts_mean(volume,20)),1)` |
| 2 | active | 外部 | external | -0.0244 | -0.3465 | +0.1800 | 2026-10-19 | `div(ts_std(volume,20),ts_mean(volume,20))` |
| 3 | active | 外部 | external | -0.0897 | -0.3257 | +0.0991 | 2026-10-19 | `div(sub(high,low),close)` |
| 4 | active | 外部 | external | -0.0268 | -0.2953 | +0.0721 | 2026-10-19 | `div(div(sub(high,low),close),add(ts_mean(div(sub(high,low),close),20),0.000000000001))` |
| 6 | active | 外部 | external | -0.0534 | -0.3482 | +0.0116 | 2026-10-22 | `div(sub(div(sub(add(open,close),abs(sub(open,close))),2),low),open)` |
| 8 | active | 外部 | external | -0.0120 | -0.1990 | +0.0095 | 2026-10-22 | `ts_corr(volume,ref(volume,1),10)` |
| 9 | active | 外部 | external | -0.0365 | -0.3486 | +0.0166 | 2026-10-22 | `log(div(ts_mean(volume,20),add(ts_mean(volume,60),0.000000000001)))` |
| 10 | active | 外部 | external | -0.0202 | -0.2842 | +0.0371 | 2026-10-22 | `ts_corr(volume,sub(div(close,ref(close,1)),1),20)` |
| 11 | active | 外部 | external | -0.0333 | -0.4216 | +0.0148 | 2026-10-22 | `sub(div(ts_mean(div(sub(high,low),close),5),ts_mean(div(sub(high,low),close),20)),1)` |
| 12 | active | 外部 | external | -0.0234 | -0.3748 | +0.0212 | 2026-10-25 | `div(ts_max(volume,10),ts_sum(volume,10))` |
| 267 | active | 价量 | proposal | -0.0327 | -0.3733 | +0.0042 | 2026-11-01 | `ts_corr(close, volume, 10)` |
| 274 | active | 流动性 | proposal | -0.0302 | -0.2270 | +0.0203 | 2026-11-01 | `log(volume)` |
| 5 | retired | 外部 | external | -0.0257 | -0.3177 | +0.0062 | 2026-10-22 | `ts_corr(close,log(add(volume,1)),5)` |
| 7 | retired | 外部 | external | +0.0000 | +0.0000 | +0.0039 | 2026-10-22 | `ts_corr(close,volume,10)` |

合计 12 active + 2 retired；60 日 RankIC 均值全负（−0.012 ~ −0.090），|ICIR60| 0.199 ~ 0.422。

## 2. 供给漏斗（`[实测]`，harvest API + 状态文件）

- 累计候选 2,444 → 不可译 1,772（72.5%）+ 转写错误 19 → 进闸 653 → 入库 12（1.8%）。
- 进闸后拒收 641：闸② 490 / 闸④ 88 / 闸⓿ 26 / 闸⑤ 22 / 闸① 15 / 闸③ 0。
- 最近入库：2026-11-01（提案月批 2 条）；2026-11-27 16:31 收割作业运行 54ms 零新增；外部生成器 `desired_running=false`（自 2026-10-29 23:00）。

## 3. 不可译分桶（`[实测]`，关键词规则非互斥处按首中优先序）

| 桶 | 条数 | 说明 |
|---|---|---|
| llm·横截面/矩阵/回归/市场 | 759 | detail 含 横截面/截面/PCA/主成分/回归/矩阵/市场/大盘 |
| llm·epsilon 无值 | 65 | ε 命中且非截面类 |
| llm·算子不支持等 | 114 | 其余（滚动偏度/中位数/分位数/条件分支等点名） |
| translator·复杂度超限 | 814 | 自由参数/深度/字段上限 |
| translator·语法等 | 20 | 一元负号 14 + 括号 6 |

**复杂度桶去重口径可解锁复核：398 条**（Number 节点 >3、按 Qlib 表达式正则去重后 ≤3、不超深度/字段；占复杂度桶 49%、全部不可译 22.5%）。

## 4. 选股基线（`[实测]`）

最新成功批：2026-11-27 / pool=main / status=ok / selection_mode=factor；l1_passed=3737，l2_top=100，l3_keep=100，pool_size=29。

L2 Top-100 代码序列（rank 升序，容器内只读 l2_candidates）：

`600007 002244 600938 003004 605168 000429 601808 600059 600825 600674 002267 603939 601229 001965 600801 600803 605507 600348 600350 601666 600908 600598 600971 601187 000858 603995 600511 600027 600968 601001 600903 603288 601139 600377 600663 000895 601825 000423 601963 001201 688617 601577 600642 605499 601997 601665 600035 002032 601518 600551 605296 603268 000708 600969 002059 603338 001979 600323 600886 600461 600546 002839 600777 000937 600422 000498 003006 600048 000729 000915 601155 002966 600085 001872 600507 601225 603833 002510 605305 600519 600192 601006 603323 600508 688621 002907 600928 600018 301087 601857 600780 000927 600023 002958 600062 001914 600795 002541 000999 600039`

## 5. 迁移确认（delta tasks 1.3）

`/api/factors` 的 category 分布仍为 `外部×12`（另 `流动性×1`、`价量×1`）⇒ `update-harvest-factor-category` 的「生产迁移执行」（其 tasks 5/5，未勾）**未执行**。delta §10（同构重提桶归属）实施前须先执行该迁移；本 P1（§1–§5）不依赖。

