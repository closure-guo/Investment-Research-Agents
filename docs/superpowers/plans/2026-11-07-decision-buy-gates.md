# 决策买入闸门修复（update-decision-buy-gates）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** 按 OpenSpec delta `update-decision-buy-gates` 拆除两个实验证实的买入闸门（A 入场时点 / B 止损几何×无协商回路），合成回归四案例验收后发布生产提示词。

**Architecture:** 全部改动在提示词契约层 + 滞回检查一处代码收窄；管线结构、价位校验器、结算口径（decision-outcome）不动。HORIZON_CONTRACT 单一来源改写后三份 md 同步（契约测试拦漂移）。

**Tech Stack:** Python 3.12 venv（`.venv/Scripts/python.exe`）、pytest、ruff、openspec、scripts/run_synthetic_case.py。

## Global Constraints

- 分支 `feat/decision-buy-gates-20261107`（基线 = feat/zero-buy-round2-p0 @ 1206d149）；origin/main 禁 push，提交走 PR。
- 规范权威：本变更只经 delta 编辑（已建 `openspec/changes/update-decision-buy-gates/`，validate --strict 已过）；禁止手改 `openspec/specs/`。
- 提示词权威源 `src/finance_agent/prompts/*.md`；发布必须走 `scripts/deploy_prompts.py`（prompt-deploy-consistency）。
- 预提交缓存锁坑：pre-commit PermissionError 时等价运行 `ruff check` + `ruff format --check` 后 `--no-verify`。
- 实验纪律：一次只动一个变量；运行期禁碰任何活跃 SQLite（监控走 API/日志）；生产库跑前后计数核验。
- 成本闸：合成回归四案例 ≈40 万 tokens；超 60 万停止并报告。
- 四态 schema（INSUFFICIENT_DATA）不在本变更（另立 delta，涉及交互面）。

---

### Task 1: 周期契约段改写 + 三决策提示词改写（contracts.py / trader.md / risk_judge.md / fund_manager.md + 契约测试）

**Files:**
- Modify: `src/finance_agent/prompts/contracts.py`
- Modify: `src/finance_agent/prompts/trader.md`
- Modify: `src/finance_agent/prompts/risk_judge.md`
- Modify: `src/finance_agent/prompts/fund_manager.md`
- Modify: `tests/test_prompt_contracts.py`

**Interfaces:**
- Produces: `HORIZON_CONTRACT` 新字面（三 md 逐字一致由既有测试断言）；trader 新增 `## 止损锚定与仓位预算` 段；risk_judge 新增 `## 否决纪律与参数协商` 段。

- [ ] **Step 1 写失败测试**（`tests/test_prompt_contracts.py`）：

```python
def test_horizon_contract_is_settlement_not_gate():
    """周期契约段=纯结算口径：无催化剂义务、无长线禁令，有错配披露。"""
    text = HORIZON_CONTRACT
    assert "结算口径" in text and "而非决策门槛" in text
    assert "MUST 说明催化剂" not in text
    assert "MUST NOT 作为" not in text and "买入主因" not in text
    assert "窗口" in text and "OUTCOME_DEFAULT_HORIZON_DAYS" in text

def test_trader_has_stop_anchoring():
    md = _load("trader")
    assert "止损锚定与仓位预算" in md
    assert "max(2×日波动" in md or "max（2×日波动" in md
    assert "自由选边" in md

def test_risk_judge_veto_discipline():
    md = _load("risk_judge")
    assert "否决纪律与参数协商" in md
    for kw in ("财务造假嫌疑", "退市", "流动性枯竭", "可执行参数缺口", "等比下调"):
        assert kw in md
    assert "倾向 hold/watch" not in md  # 旧倾向废除

def test_confidence_mid_band_deanchored():
    for name in ("trader", "risk_judge", "fund_manager"):
        md = _load(name)
        assert "示例 0.55" not in md
        assert "向示例值收敛" in md
```

- [ ] **Step 2 运行确认失败**：`uv run pytest tests/test_prompt_contracts.py -q` → 新断言 FAIL。
- [ ] **Step 3 最小实现**：

  1. `contracts.py` 的 `HORIZON_CONTRACT` 整体替换为：

```python
HORIZON_CONTRACT = """## 周期契约

本决策按 **T+20 个交易日相对沪深300 超额收益（±2% 中性带）** 结算考核（口径与结算配置
OUTCOME_DEFAULT_HORIZON_DAYS 联动，该配置变更时本段须同步更新）。该窗口是**结算口径
而非决策门槛**：
- buy 决策 SHALL 在 reasoning 中披露主要论据的兑现窗口与考核窗口的匹配情况；兑现期
  超过考核窗口的长线论据（估值回归、产能释放等）可作为买入依据，但 MUST 同步说明
  窗口错配风险（考核期内可能不体现相对超额收益）。
- 催化剂缺失本身不构成不买理由；watch/hold/sell 语义不变，watch 的 reeval_triggers
  按既有语义给出周期内触发条件。"""
```

  2. trader/risk_judge/fund_manager 三 md 的「## 周期契约」段整段替换为上述字面（逐字一致，既有测试拦漂移）。
  3. trader.md 在「## 决策语义」之前插入：

```markdown
## 止损锚定与仓位预算

buy/short 型方案的 stop_loss MUST 预算锚定，MUST NOT 在止损参考带（context.price_levels
的 stop_band_long）内无视噪声自由选边：
- 止损 = max(2×日波动, 最近结构位)——日波动 = 年化波动率 ÷ √252（context 风控指标
  可得）；最近结构位取现价下方最近的均线/平台支撑。
- 参考带由 ATR 派生：带上沿若窄于 1.5×日波动即为「噪声内止损」，MUST 下移止损至
  ≥2×日波动或最近结构位（修正后价位仍须落在参考带 sanity 范围内，否则会被价位校验打回）。
- position_size 与止损距离预算联动：止损越窄仓位越轻（单笔风险预算不变），reasoning
  MUST 给出「止损距离 → 仓位档位」的预算依据。
- 方向纪律：质量/估值/趋势条件成立时 MUST 产出可执行价位方案（entry/stop/target +
  position_size）——「无催化剂」按周期契约披露窗口错配即可、「止损窄」按本节锚定规则
  放宽、「数据部分缺失」按置信度分档降档，三者 MUST NOT 直接导向 watch；仅当条件确实
  不满足时才 watch，且 inaction_reason MUST 指名具体未满足条件。
```

  4. risk_judge.md：将「- 当多空/风险论据证据均衡时，倾向 hold/watch 而非强行买卖」替换为「- 证据均衡（多空对峙）时：reasoning 如实披露对峙点即可，方向裁决按下方『否决纪律与参数协商』执行，不因均衡本身倾向观望」；在「## 决策语义」段后新增：

```markdown
## 否决纪律与参数协商

方向否决（把 buy/sell 改为 hold/watch）仅限致命白名单：财务造假嫌疑、退市/面值退市
风险、流动性枯竭无法成交、受众无法交易、关键数据被判定不可信。MUST NOT 以「止损窄于
噪声」「赔率不足」「数据部分缺失」「无催化剂」否决方向——这些是执行参数问题，不是
方向问题。执行参数不可用时 MUST 二选一：
①联合修正：放宽止损至 ≥2×日波动或最近结构位，并等比下调仓位档位（单笔风险预算不变；
  价位继承规则允许调整数值，修正后价位须落在参考带 sanity 范围内）；
②显式缺口：维持方向（buy/sell + light 档），reasoning 申报「可执行参数缺口」——写明
  满足可执行所需的止损宽度与仓位档位。
仅当放宽后止损仍超出单笔风险预算或仓位低于 light 档下限时才可输出 watch/hold，且
inaction_reason MUST 指名缺口内容。MUST NOT 既不修正也不申报缺口就输出观望。
```

  5. risk_judge.md「证据均衡与决策滞回」条目改写为：

```markdown
- 证据均衡与决策滞回：若 context 含「证据均衡带：是」，reasoning MUST 如实披露多空
  对峙点（仅披露义务，不约束方向）；方向翻转（与近窗决策史前向不同，执行↔观望或
  buy↔sell）MUST 申报触发翻转的增量事实，无增量请维持近窗前向方向。系统会复核：未申报
  增量的翻转将被打回重申一次，重申仍无则维持前判；非翻转的执行动作（含均衡带内首次
  建仓）以价位逻辑与既有证据为充分依据，不受增量检查约束
```

  6. confidence 中档示例去锚（三处）：`0.4–0.7 中等（示例 0.55）：…` → `0.4–0.7 中等（0.45–0.65 区间内按证据组合取值）：…`；并在各分档判据块末行（「reasoning MUST 引用支撑所选档位的证据…」）追加「MUST NOT 向示例值收敛——同一证据组合在不同标的上应产生不同置信度」。

- [ ] **Step 4 运行确认通过**：`uv run pytest tests/test_prompt_contracts.py -q` 全绿；`uv run ruff check && uv run ruff format --check` 干净。
- [ ] **Step 5 提交**：`git commit -m "feat(prompts): 周期契约改结算口径 + 止损锚定/否决纪律/置信度去锚（update-decision-buy-gates T1）"`

---

### Task 2: 分析师 horizon_hint 括注去除

**Files:**
- Modify: `src/finance_agent/prompts/{fundamental,technical,macro,sentiment}_analyst.md`（各 1 处括注）
- Modify: `tests/test_prompt_contracts.py`

- [ ] **Step 1 失败测试**：

```python
def test_analysts_no_decision_verdict_on_horizon():
    for name in ("fundamental_analyst", "technical_analyst", "macro_analyst", "sentiment_analyst"):
        md = _load(name)
        assert "不能支撑买入主因" not in md
        assert "horizon_hint" in md  # 申报义务保留
```

- [ ] **Step 2 确认失败** → **Step 3**：四处删除括注「（决策层按 T+20 个交易日考核，long 档论据不能支撑买入主因）」，其余不动 → **Step 4 全绿** → **Step 5 提交** `feat(prompts): 分析师 horizon_hint 去决策判语（T2）`。

---

### Task 3: 证据均衡带收窄（execution/hysteresis.py + 关联检查点）

**Files:**
- Modify: `src/finance_agent/execution/hysteresis.py`
- Modify: 关联调用/测试（实现时以 `grep -rn "needs_reaffirm\|downgrade_balanced" src tests` 圈定；已知 `src/finance_agent/nodes/risk.py` 消费其结果）
- Test: 既有滞回测试文件（grep 圈定，先改断言再改实现）

**Interfaces:**
- Consumes: 现有 `needs_reaffirm = (balanced_zone and actionable and not incremental_claimed) or (flip and not incremental_claimed)`（hysteresis.py:46）
- Produces: `needs_reaffirm = flip and not incremental_claimed`；`HYSTERESIS_DOWNGRADE_BALANCED` 常量保留（state 兼容）但均衡带单独不再触发降级路径；`decision_hysteresis` state 键与 balanced_zone 字段保留。

- [ ] **Step 1 失败测试**：新增/改写用例——均衡带成立 + 近窗无历史（首次建仓）+ buy 无增量 → `applied == ""`（放行，不打回不降级）；均衡带成立 + 近窗前向 watch + buy 无增量 → 仍走打回/维持前判（方向滞回不变）。
- [ ] **Step 2 确认失败** → **Step 3 最小实现**（hysteresis.py 条件收窄；nodes/risk.py 侧若有均衡带单独打回反馈路径一并收窄，以测试为准）→ **Step 4 全绿**（全套件 `uv run pytest tests/ -q` 中滞回相关 + 目标面）→ **Step 5 提交** `feat(hysteresis): 均衡带增量义务收窄至方向翻转（T3）`。

---

### Task 4: 合成回归四案例（验收门）

**Files:** 无代码改动；产物 `reports/synthetic/v31-production-postfix.json` / `v4-production-postfix.json` / `v5-production-postfix.json` / `v31-bprime-defect-postfix.json`（命名沿用）+ 报告 `docs/evals/decision-buy-gates-regression-2026-11-07.md`

**前置**：确认提示词 loader 的 Langfuse 优先/本地回退机制——若存在本地优先开关（grep `load_prompt_with_meta` 的 env/参数），回归用本地新提示词跑（不发布）；若无，则先 Task 5 发布再回归（夜间窗口，回滚 = git revert 后重发旧文本）。

**运行**：`scripts/run_synthetic_case.py --case {perfect,perfect_cooled,perfect_tight,defect}`（**不带 --variant**，即新生产提示词）。

**判据（预注册）**：
- perfect_cooled（v4）→ **必须 buy**（该买的能买）；
- perfect_tight（v5）→ **必须 buy**（协商回路生效；fallback：watch 且 inaction_reason 显式申报「可执行参数缺口」——出现即判 PARTIAL 并披露）；
- defect → **必须 sell**（不该买的仍不买）；
- perfect（v3.1 超买）→ buy 或 watch 均可，但 reasoning/inaction_reason **不得引用**「催化剂/证据均衡/止损窄于噪声」类理由（只允许时点/价位条件）；
- 全部运行变体注入为空（生产提示词）；生产库 predictions/sessions 计数前后不变。

- 任一判据不达 → 修复循环（回到对应 Task）后重跑该案例（单案例 ≈10 万 tokens，重跑上限 2 次，超限停下报告）。

---

### Task 5: 发布与收尾

- [ ] `uv run python scripts/deploy_prompts.py` 发布 Langfuse production（前查：无 running 会话/作业——夜间窗口执行）；记录新版本号清单。
- [ ] 回滚路径写进报告：`git revert` 提示词提交后重跑 deploy 即回退（Langfuse 版本化，无状态迁移）。
- [ ] `openspec/changes/update-decision-buy-gates/tasks.md` 回填勾选；进度台账 `.superpowers/sdd/progress.md` 更新。
- [ ] 实验报告落 `docs/evals/`；记忆更新；推送分支开 PR（base=feat/zero-buy-round2-p0，堆叠）。

## Stopping Points

1. Task 4 回归出现判据不达且 2 次修复循环未过 → 停，报告归因。
2. 成本超 60 万 tokens → 停。
3. deploy 前发现 running 会话/作业 → 等（红线：交易窗口禁重启；deploy 不重启进程但发布即生效，仍在无会话窗口执行）。
