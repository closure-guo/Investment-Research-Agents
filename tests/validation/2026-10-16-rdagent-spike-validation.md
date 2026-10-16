# RD-Agent 外部供给线 spike 验证报告（unify-quant-research-architecture P0-3）

- 日期：2026-10-16（环境 ① ② 于 10-16 白天；③ 晚 19:52–20:12；④⑤ 晚 21:00–22:40）
- 变更：`openspec/changes/unify-quant-research-architecture`（tasks P0-3「RD-Agent spike」六步）
- 结论先行：**供给线③不投用（当前形态）——翻译覆盖不足（4/10，缺 `Ref`）+ 可翻译者 0/4 过闸；
  导入通道保留（已入主规范库，成本零）、架构零改动**（delta 任务原话「不达标则导入通道保留、
  供给线不投用」）。
- 本机环境：Windows 11 + WSL2 + Docker Desktop 29.6.1；LLM 网关 bigmodel glm-5.3-flash（spike 期配置）

## ① 环境（WSL2 distro + Docker 集成 + 独立环境）

- distro `rdagent`（Ubuntu 24.04，`wsl --import` 自 NJU 镜像 rootfs）+ Python 3.11.17（uv 工具链）；
  rdagent（pip/TUNA）+ **pyqlib 0.9.7** + conda env `rdagent4qlib`（py3.10，qlib 评估依赖）——
  不进项目 3.14 环境（任务约定）。
- **唯一卡点**：fin_factor 循环 init 需 Docker SDK，Docker Desktop WSL 集成对 `rdagent` distro
  未注入（settings-store.json CLI 修改 + 两种重启顺序均无效）→ **GUI 勾选 WSL Integration 后解锁**
  （`/mnt/wsl/docker-desktop-bind-mounts/rdagent/docker.sock` 可见）。
- 三坑与绕过（脚本含修复，留档 `tests/scripts/spike/README.md`）：
  1. 镜像构建内 `git clone microsoft/qlib` 被墙 → Dockerfile 换 ghfast 镜像（8MB/s）；
  2. RD-Agent 默认 embedding `text-embedding-3-small` 在 bigmodel 不存在（错误码 1211）→
     `EMBEDDING_MODEL=openai/embedding-3`（实测 dim 2048 ✓）；
  3. LiteLLM 对 `openai/` 前缀回落读 `OPENAI_API_KEY` → `.env` 补 OPENAI_API_KEY/BASE。
- 环境脚本：`tests/scripts/spike/{setup-rdagent,setup-conda,config-llm,patch-runtime-info,launch-fin-factor}.sh`

## ② 数据（社区数据集先行）

- `chenditc/investment_data` A 股 qlib_bin（2026-10-15 版，2000–2026-10-15，850MB）→
  `~/.qlib/qlib_data/cn_data`。**未做自有导出器**（任务约定：社区数据质量不足时再另立 delta）。

## ③ 运行（`rdagent fin_factor` 单循环）

- **单循环 5/5 步 100% 完成**：direct_exp_gen → coding → running → feedback → record
  （日志 `fin_factor-run9.log`，19:52 起 20:12 完成；LLM 每次调用 `openai/glm-5.3-flash`）。
- 产出 10 个候选（workspace `factor.py` 实现 + LaTeX formulation），LLM 生成代码在 local_qlib
  容器内真实数据评估（**47,378 非空因子值**）；CoSTEER 判 Volatility_20 实现 **SUCCESS**
  （公式/窗口/ddof/输出格式逐条核对正确）。
- 候选族分布（10 条）：动量/反转族 4（MOM20/Momentum_20/Reversal_5/REV5）、波动族 2
  （VOL20/Volatility_20）、量比族 2（VolumeRatio_20/VRATIO20）、乖离 1（BIAS20）、
  日内结构 1（ClosePosition）。

## ④ 翻译（「外部表达式翻译契约」TDD 实现）

- 落点：spike 工具链 `tests/scripts/spike/qlib_translate.py`（design.md Non-Goals：**不进生产代码**）；
  TDD 先红后绿：`uv run pytest tests/scripts/spike/test_qlib_translate.py -q` → **23/23 绿**
  （契约三场景 + 映射审计 + 语法/窗口/负数/复杂度边界）。
- **映射表（43 算子 + 7 字段，随产物留档）**：equivalent 11 / not_equivalent 2 / missing 30。
  逐算子核对 **pyqlib 0.9.7 `qlib/data/ops.py` 源码**（行号在表内），关键发现：
  - Rolling 基类 `min_periods=1`（L752）——热身期输出部分窗口值（本系统 `ts_*` 为
    min_periods=window → NaN）；等价基准取 formulation 语义，该差异在映射表显式声明；
  - `Ref` = `series.shift(N)`（L793）→ **本 DSL v1 无 ref 算子 → missing**（动量/反转/收益率族
    不可翻译的单一主因）；
  - `Rank` = 滚动 pct 秩（L1156）——**非截面排名**，与 `ts_rank` formulation 等价（据此修正
    delta 规格场景例：`截面排名 Rank` → `滚动分位数 Quantile`；改后 `validate --strict` 通过）；
  - `Max/Min` = 滚动窗口算子（L951/L999）——与本系统逐元素二元 `max/min` 同名不同义 →
    **not_equivalent（拒绝机械替换，spec 场景 3）**；
  - `Div`=`np.divide`（x/0→±inf）、`Log`=`np.log`（x=0→−inf）边界约定声明；
    窗口 `N=0` 为 expanding、`0<N<1` 为 ewm → 一律拒绝（语义不同）。
- 候选批裁决（`candidates_2026-10-16.json`，Qlib 表达式由 formulation+实现确定性给出）：

| 候选 | Qlib 表达式 | 裁决 |
|---|---|---|
| MOM20 / Momentum_20 | `$close / Ref($close, 20) - 1` | 不可翻译（Ref missing） |
| Reversal_5 / REV5 | `$close / Ref($close, 5) - 1` | 不可翻译（Ref missing） |
| VOL20 / Volatility_20 | `Std($close / Ref($close, 1) - 1, 20)` | 不可翻译（Ref missing） |
| VolumeRatio_20 / VRATIO20 | `$volume / Mean($volume, 20)` | `div(volume,ts_mean(volume,20))` |
| BIAS20 | `$close / Mean($close, 20) - 1` | `sub(div(close,ts_mean(close,20)),1)` |
| ClosePosition | `($close - $low) / ($high - $low)` | `div(sub(close,low),sub(high,low))` |

**翻译覆盖 4/10**（6/10 全部因缺 `Ref`；RD-Agent 自选 SOTA 族恰在不可翻译集）。

## ⑤ 过闸（导入通道，生产五道闸 + 150 日深面板）

- 运行：`uv run python tests/scripts/spike/run_translation_import.py --candidates ... --out reports/spike-rdagent-translation-20261016.json`
- 摘要：**`{admitted: 0, rejected: 4, errors: 6}`**（空池快照：因子库 0 条、面板 188 交易日）
- 逐条读数与**分桶归因**（不把不同桶混读）：

| 桶 | 条数 | 读数 | 归因 |
|---|---|---|---|
| errors（gate=None） | 6 | `DSLParseError: 意外的字符 '$'` | **设计内**：不可翻译候选按外部表达式形态入 errors 桶（spec「不可翻译候选计入 errors 桶」逐字） |
| 闸④ NO_POOL_INCREMENT | 2 | 量比对（同一 DSL 表达式） | 空池语义下增量 = 验证段 IR(候选) ≤ 0——与内置 `turnover_proxy` 同族倒在闸④ |
| 闸② WEAK_ICIR | 1 | BIAS20 | 本窗口短周期乖离/反转信号弱（与内置 `reversal_5` ICIR≈−0.008 同族） |
| 闸① LOW_COVERAGE | 1 | ClosePosition | **数据缺口归因（非因子质量）**：本系统快照回填行 `high/low` 覆盖 **0.0%**（2026-01～09 全空），仅 10 月日批行有（29.8%）——回填列契约不含 high/low。该候选在本系统面板上**无法被公平评估** |

- 重复候选对（VolumeRatio_20 ≡ VRATIO20）两条独立拒收（未触闸 0——库内无同构对象；两者均未入库）。

## ⑥ 结论（供给线③是否投用）

**不投用（当前形态）**，依据三条（均可复现）：

1. **翻译覆盖不足**：4/10 可翻译，缺失面全部集中在 `Ref`（后移引用）——动量/反转/波动族
   （外部研究最常见的因子形态）整体不可表达；
2. **可翻译者 0/4 过闸**：唯一进入闸口裁决的三条表达式全部拒收，且拒收原因为实质统计读数
   （弱 ICIR / 无样本外增量）或数据缺口（high/low），无一条因翻译损耗被误伤；
3. **外部择优与硬闸分歧实证**：RD-Agent 侧 CoSTEER 判 SUCCESS 的 Volatility_20 与本系统内置
   `volatility_20` 重合（即便可翻译也无增量），且其 SOTA 择优无多重检验校正（对标分析 §4）——
   「外部 SOTA ≠ 过闸因子」在本批得到验证。

按 delta 既定处置：**导入通道保留**（`source=external` 通道已入主规范库，日常零成本）、
**供给线不投用**、**架构零改动**。若未来扩 DSL（`ref` 算子）后可重跑本批输入做增补读数；
供给线正式投用须另立 delta（含运维成本评估：WSL distro + conda env + 补丁维护）。

## 遗留（挂账，不阻塞本 delta 收口）

- **回填扩列（high/low）**：回填行缺 high/low 使日内结构类因子在准入面板结构性不可评估——
  数据面候选改进项（另立 issue / delta；回填列契约从「五列+amount」扩至八列）。
- **扩 DSL `ref` 算子**：解锁 6/10 不可翻译族的前置；涉新算子语义 + 复杂度上限 + 闸口影响，
  须独立 delta（本 spike 的映射表差异说明已备参考）。
- spike 环境维护成本（WSL distro + conda env + runtime-info 补丁）与 MLflow 弃用告警（record 步，无害）。
- 运行中已知坑：`docker compose restart` 不重读 env_file（改 .env 需 `up -d`）；
  MSYS 路径转换对 `wsl` 传参的干扰（脚本内已用引号/`MSYS2_ARG_CONV_EXCL` 规避）。

## 复现命令

```bash
uv run pytest tests/scripts/spike/test_qlib_translate.py -q                       # ④ 翻译器 23/23
uv run python tests/scripts/spike/run_translation_import.py \
  --candidates tests/scripts/spike/candidates_2026-10-16.json --dry-run           # 裁决表
uv run python tests/scripts/spike/run_translation_import.py \
  --candidates tests/scripts/spike/candidates_2026-10-16.json \
  --out reports/spike-rdagent-translation-20261016.json                          # ⑤ 过闸读数
```
