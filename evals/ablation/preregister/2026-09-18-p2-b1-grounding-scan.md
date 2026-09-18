# 预登记：P2-B1 无源增量 grounding 扫描（bg-v1）

- name: p2-b1-grounding-scan
- date: 2026-09-18
- status: registered-before-run

## 0. 机器可读字段

```yaml
name: p2-b1-grounding-scan
rubric: bg-v1
method: judge            # 校准门控适用（≥0.80 才可进结论）
universe: 64             # 20 标的 × 第 1 轮 kind=data 空方论点
llm_calls_cap: 100
calibration: 采样协议 v2（AI 全量预判 + 低/中把握全审 + 高把握抽 10%）
primary: 无源断言率 = supported=false 的单元 / 64
mde: 不适用（描述性诊断扫描，不作层增量裁决、不进裁剪决策）
stop_rules:
  - 解析失败率 > 10% → 停，先修解析
  - 校准一致率 < 0.80 → 读数作废，不进任何结论
```

## 1. 动机与口径

B1 阈值标定判死字面路线、judge 新增维度封顶 0.75（§19.3）后，「无源增量」改走 **grounding
口径**：不问"相对 16 条发现是否新增"，问"**这条被自标为 kind=data 的论点，其事实断言能否
被辩手当时的全部输入（4 份分析师摘要）支撑**"。这是标准 NLI 形态（前提=辩手真实输入），
比新增判定干净一个量级。

**为什么限第 1 轮**：辩手 r1 的输入恰为且仅为 4 份摘要（`debate.py::_build_debate_context`）；
r2+ 还能看到辩论历史，断言可能来自对方发言——对摘要判可支撑性会误伤。宇宙 = r1 ∧ kind=data
= 64 单元（20 标的）。

**首例**：000858::b1::2「机构资金持续撤离」——摘要零资金流内容且方向相反（板块净流入
11.09 亿）→ supported=false（无源断言）。

## 2. 判定 prompt 要点（bg-v1）

- 事实断言全部可在摘要找到依据（原文或直接换算）→ supported=true
- 存在无依据断言（无中生有的数字/资金流/事件，**或与摘要方向相反**）→ supported=false +
  `unsupported_part` 摘出断言原文
- 合理推断不算无源（断言本身有源即可）
- 输出含 `confidence: high|medium|low`（采样协议 v2 的分层依据）

## 3. 读数用法与红线

- 读数 = 无源断言率（描述性）+ 逐条无源断言清单；进 incident/报告时**只作桶归因证据**，
  不触发对辩手的自动处置（先分桶后终裁，incident 026 纪律）
- 校准：低/中把握行全审 + 高把握行抽 10%，一致率 ≥0.80 才允许进结论；未过门 = provisional
- 成本上限 100 次调用；判定即时落盘按 unit_id 缓存（judged-bg-v1.jsonl）

## 4. 与登记主张的关系

B1 主指标不变（新增率=人工区间 0.45–0.55；吸收率 judge v1 0.875）。本扫描是 B1 的
**解释性子腿**（增量里有多少是无源），不新增主指标、不进层增量比较。
