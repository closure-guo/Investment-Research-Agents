# 项目工作流

> 本手册是日常开发工作流规范。路由规则见 [AGENTS.md](../AGENTS.md)，本手册提供各条路线的详细执行步骤、产物模板和衔接细节。
>
> E2E 测试的完整代码模板（playwright.config.ts、LLM stub、spec 示例、CI yaml）见独立文档 [docs/design/E2E测试实现方案-finance_analysis_agent.md](design/E2E测试实现方案-finance_analysis_agent.md)，本手册 §5.6 收录其基础设施与工具链纪律。

---

## 1. 全景图

```
任务进站（先查 AGENTS.md 任务路由表判定类型）
  │
  ├─ 新功能 / 行为变更 ──────────────────────────────────────────┐
  ├─ 修 bug · 意图变更 / 行为未定义 ────────────────────────────┤
  │                                                              ▼
  │  ① OpenSpec delta 提案                                       │
  │     openspec/changes/NNN-特性名/                             │
  │     ├── proposal.md   ← 为什么、改什么                       │
  │     ├── specs/        ← delta spec（ADDED/MODIFIED/REMOVED） │
  │     ├── design.md     ← 技术方案（可选）                     │
  │     └── tasks.md      ← 粗粒度验收 checklist                 │
  │                    │                                         │
  │                    ▼                                         │
  │  ② Superpowers: writing-plans                                │
  │     读 delta spec → 拆成 TDD 粒度的执行计划                  │
  │     交互类变更: 拆 E2E spec 任务卡                           │
  │     （playwright-test-generator 真实探索 → 人工审批场景）    │
  │     产物: docs/superpowers/plans/YYYY-MM-DD-<feature>.md     │
  │                    │                                         │
  │                    ▼                                         │
  │  ③ Superpowers: subagent-driven-development                  │
  │     每个任务: 派发 implementer → task-reviewer → 修复循环    │
  │     E2E spec 任务: scan.sh 抓 P0 → e2e-reviewer 深审         │
  │     全部任务完成后: 派发 final code-reviewer                 │
  │                    │                                         │
  │                    ▼                                         │
  │  ④ Superpowers: verification-before-completion               │
  │     运行验证命令 → 读取输出 → 确认通过 → 才能声称完成        │
  │                    │                                         │
  │                    ▼                                         │
  │  ④.5 E2E 门禁（仅交互类变更）                                 │
  │     cd e2e && npx playwright test（stub 套件）               │
  │     全绿 → 进 ⑤                                             │
  │     红 → playwright-debugger 诊断 → 打回 ③                  │
  │     禁止带病进人工验证                                       │
  │                    │                                         │
  │                    ▼                                         │
  │  ⑤ 人工验证（抽查 + 主观项确认）                               │
  │     交互行为变更必须有人工验证环节                            │
  │     报告落 tests/validation/                                  │
  │                    │                                         │
  │                    ▼                                         │
  │  ⑥ OpenSpec: sync + archive                                  │
  │     delta 合并进 specs/ → changes/NNN-x 移入 archive/        │
  │     tasks.md 全勾 + 验证通过 + 人工验证报告 → 才能 archive   │
  │                                                              │
  ├─ 修 bug · 意图不变（spec 对、代码错） ──┐
  │     Superpowers: systematic-debugging      │
  │     4 阶段: 根因 → 模式 → 假设 → 实现      │
  │     红线: 先写复现测试（红），再修（绿）    │
  │     不触碰 openspec                         │
  │                                             │
  ├─ 重大架构决策 ──→ 落 docs/adr/（编号递增；agent 可新建/编辑，改结论须追加修订记录）
  │
  └─ 小改动（typo/文案/配置）──→ 直接改，不走任何管线
```

---

## 2. 路由索引

任务路由由 `AGENTS.md` 统一管理。本文档不重复路由分类逻辑，只提供各条路线的执行步骤。

| 路由目标 | 本文档对应章节 |
|---|---|
| 新功能 / 行为变更完整管线 | §3 |
| 修 bug · 意图不变 | §4.1 |
| 修 bug · 意图变更 | §4.2 |

**交互类变更判别**：delta 涉及前端 UI、SSE 流式、会话切换、状态流转中任一者，即为交互类变更，走 §3 完整管线（含 §3.5 E2E 门禁）；纯后端逻辑变更（指标计算、数据管道、prompt 调整）不适用 §3.5。

**测试分工**：单元/集成测试走 Superpowers TDD 五步纪律（§3 Step 2 计划模板）；浏览器交互层的 E2E spec 生成、审查、诊断全部由 e2e-skills 工具链接管（§5.6），不临时手写、不走其他测试方式。

---

## 3. 新功能完整流程（SOP）

### Step 1: OpenSpec delta 提案

**前提**: `openspec/` 已初始化（`openspec init`）。如未初始化，先执行。

**操作**:

1. 确认 change-id：kebab-case，动词开头（`add-`、`update-`、`remove-`、`refactor-`）
2. 创建变更目录 `openspec/changes/<change-id>/`
3. 编写四个工件

**工件 1: proposal.md** — 为什么、改什么

```markdown
# Proposal: <change-id>

## Why
1-2 句话说明问题或机会。解决什么问题？为什么现在做？

## What Changes
- 变更项 1
- 变更项 2（标记 **BREAKING** 如果是破坏性变更）

## Capabilities
- **New Capabilities**: 列出新增的 capability（每个对应 specs/<name>/spec.md）
- **Modified Capabilities**: 列出需要修改的已有 capability（从 openspec/specs/ 查已有名称）

## Impact
受影响的代码、API、依赖或系统。
```

**工件 2: specs/\<capability\>/spec.md** — delta spec（行为契约）

格式见 [§5.1 delta spec 模板](#51-delta-spec-模板)。

**工件 3: design.md** — 技术方案（可选，复杂变更才写）

```markdown
# Design: <change-id>

## Approach
2-3 段描述技术方案和架构决策。

## Alternatives Considered
- 方案 A: ...（为什么不选）
- 方案 B: ...（为什么不选）

## Risks
- 风险 1: ...（对策）
```

**工件 4: tasks.md** — 粗粒度验收 checklist

```markdown
# Tasks: <change-id>

- [ ] 验收项 1（如「流式中断恢复可用」）
- [ ] 验收项 2（如「会话切换时 SSE 正确断开重连」）
- [ ] E2E spec 已覆盖核心交互场景（仅交互类变更）
- [ ] 验收项 4（如「人工验证报告已落 tests/validation/」）
```

> tasks.md 是粗粒度验收项，不是细粒度 TDD 步骤。细粒度步骤由 Step 2 的 writing-plans 产出，做完即弃。

**验证**:

```bash
openspec validate <change-id> --strict
```

通过后才能进入 Step 2。

---

### Step 2: writing-plans — 拆执行计划

**Skill**: `superpowers:writing-plans`

**输入**: delta spec（Step 1 产物）
**输出**: `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`

**操作**:

1. 宣告: "I'm using the writing-plans skill to create the implementation plan."
2. 读 delta spec，拆成 TDD 粒度的任务
3. 每个任务包含：文件路径、接口契约、TDD 五步（写失败测试 -> 运行确认失败 -> 写最小实现 -> 运行确认通过 ->提交）
   - 交互类变更必须拆出 E2E spec 任务卡，全流程走 e2e-skills 工具链：
     1. `playwright-test-generator` 配合 Playwright MCP/CLI 真实探索浏览器，获取真实 DOM 快照（无 MCP/CLI 时 generator 退化为静态快照，探索不到弹窗和多步流程，必须先配好）
     2. 人工审批场景方案后再落 spec
     3. 从真实快照提取稳定 selector（data-testid > role+name > placeholder），禁止盲写
     4. 写失败的 Playwright spec（`e2e/tests/*.spec.ts`）→ 实现 → spec 转绿，与单测同一 TDD 纪律
   - spec 写作红线见 §5.5，工具链纪律见 §5.6
4. 自审：spec 覆盖率检查、占位符扫描、类型一致性检查
5. 保存计划文件
6. 选择执行方式（推荐 subagent-driven-development）

**计划文件格式**:

````markdown
# <Feature Name> Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development

**Goal:** [一句话描述]

**Architecture:** [2-3 句方案]

**Tech Stack:** [关键技术]

## Global Constraints
[项目级约束，逐条列出，每条包含确切值]

---

### Task 1: [组件名]

**Files:**
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py:123-145`
- Test: `tests/exact/path/to/test.py`

**Interfaces:**
- Consumes: [依赖的前序任务的接口签名]
- Produces: [后序任务依赖的函数名、参数和返回类型]

- [ ] **Step 1: Write the failing test**

```python
def test_specific_behavior():
    result = function(input)
    assert result == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/path/test.py::test_name -v`
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
def function(input):
    return expected
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/path/test.py::test_name -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/path/test.py src/path/file.py
git commit -m "feat: add specific feature"
```
````

> **红线**: 计划中禁止占位符（TBD、TODO、"add appropriate error handling"）。每一步必须包含实际代码。

---

### Step 3: subagent-driven-development — 执行

**Skill**: `superpowers:subagent-driven-development`

**输入**: Step 2 的计划文件
**输出**: 代码 + 测试 + git commits

**执行流程**:

```
读计划 → 创建 todos → 记录初始 BASE_SHA
  │
  ▼
┌─── 每个任务循环 ───────────────────────────────────────────┐
│                                                            │
│  1. 提取任务简报 (scripts/task-brief PLAN_FILE N)          │
│  2. 派发 implementer subagent                              │
│     - 传入: 任务简报路径 + 报告文件路径 + 上下文            │
│     - implementer 实现 + 测试 + 提交 + 自审                 │
│     - 返回: DONE / DONE_WITH_CONCERNS / BLOCKED / NEEDS_CONTEXT │
│                                                            │
│  3. 生成 diff 包 (scripts/review-package BASE HEAD)        │
│  4. 派发 task-reviewer subagent                            │
│     - 传入: 任务简报 + 报告文件 + diff 包 + 全局约束        │
│     - 返回: Spec ✅/❌ + 代码质量 Approved/Needs fixes     │
│                                                            │
│  5. 如有 Critical/Important 问题:                          │
│     - 派发 fix subagent 修复                               │
│     - 重新派发 task-reviewer 审查                          │
│     - 循环直到通过                                         │
│                                                            │
│  6. 标记任务完成，更新 progress ledger                     │
│                                                            │
└────────────────────────────────────────────────────────────┘
  │
  ▼
全部任务完成
  │
  ▼
派发 final code-reviewer（全分支审查）
  - 生成全分支 diff 包 (scripts/review-package MERGE_BASE HEAD)
  - 如有问题: 派发一个 fix subagent 处理全部问题
  │
  ▼
使用 superpowers:finishing-a-development-branch 完成分支
```

**E2E spec 任务卡的附加质量门禁**（嵌入上述循环，替代临时审查方式）:

- implementer 提交 spec 代码前，自跑 `scan.sh`（零 token 确定性扫描）抓 P0，P0 清零才返回 DONE
- task-reviewer 对 spec 代码执行 `e2e-reviewer`（24 反模式深审），反模式未清零不得 Approved
- E2E 运行失败时，先用 `playwright-debugger`（15 根因诊断）生成诊断报告，再派 fix subagent——禁止盲改赌修复

**implementer 四种状态处理**:

| 状态 | 含义 | 控制器动作 |
|---|---|---|
| DONE | 实现完成 | 生成 diff 包，派发 task-reviewer |
| DONE_WITH_CONCERNS | 完成但有疑虑 | 读疑虑 → 正确性/范围问题先解决 → 进入审查 |
| NEEDS_CONTEXT | 缺少信息 | 提供缺失信息 → 重新派发 |
| BLOCKED | 无法完成 | 评估: 补上下文 / 换更强模型 / 拆小任务 / 升级给人 |

**模型选择**:

| 任务复杂度 | 模型层级 | 示例 |
|---|---|---|
| 1-2 文件 + 完整 spec | 最低成本 | 纯转录 + 测试 |
| 多文件 + 集成关注 | 标准 | 多文件协调 |
| 设计判断 / 广泛代码理解 | 最强 | 架构级改动 |

> 审查任务至少用标准模型；最终全分支审查用最强模型。

**持续执行规则**: 不要在任务之间停下来问人。执行完所有任务再报告。只有 BLOCKED 无法解决、歧义阻碍推进、或全部完成时才停。

---

### Step 4: verification-before-completion — 验证

**Skill**: `superpowers:verification-before-completion`

**铁律**: 没有新鲜验证证据，不得声称任何完成状态。

**验证流程**:

```
声称之前:
  1. 确认: 什么命令能证明这个声称？
  2. 运行: 执行完整命令（新鲜的，完整的）
  3. 读取: 完整输出，检查 exit code，数失败数
  4. 核实: 输出是否确认了声称？
     - 否 → 陈述实际状态 + 证据
     - 是 → 陈述声称 + 证据
  5. 然后才能声称
```

**常见声称的验证要求**:

| 声称 | 需要的证据 | 不够的证据 |
|---|---|---|
| 测试通过 | 测试命令输出: 0 failures | 上次运行、"应该通过" |
| Bug 已修 | 原症状测试: 通过 | 代码改了、"假设修好了" |
| 需求满足 | 逐行对照 checklist | 测试通过 |
| Agent 完成 | VCS diff 显示变更 | Agent 报告 "success" |

---

### Step 4.5: E2E 门禁（仅交互类变更）

**触发条件**: delta 涉及交互行为（前端 UI、SSE 流式、会话切换、状态流转，判别见 §2）。

**生效状态**: §5.6 落地路线 P1–P4 完成后，E2E 门禁对交互类变更完全生效。

**操作**:

```bash
cd e2e && npx playwright test
```

stub 套件（后端以 `TESTING=1` 启动，独立测试库 + LLM 可控 stub，见 [docs/design/E2E测试实现方案-finance_analysis_agent.md](design/E2E测试实现方案-finance_analysis_agent.md)），确定性、秒级-分钟级完成。

**通过标准**: 全绿。

**失败处理**: 任一 spec 红 → 先用 `playwright-debugger` 生成根因诊断（证据在 `e2e/playwright-report/` 的 trace/截图），再打回 Step 3 修复，禁止带病进人工验证。

**证据要求**: playwright-report 路径记入验证材料（人工验证报告 §异常记录 引用）。

**红线**: 禁止为了让门禁变绿而删除或放宽断言——断言被改弱必须能在 diff 中解释原因。

**@live 套件（真 LLM）**: 不进门禁，由 CI nightly 定时跑，用于发现 stub 与真实模型行为的漂移；确认漂移后回流修正 stub 或 spec。

---

### Step 5: 人工验证

**触发条件**: 任何交互行为变更（前端 UI、SSE 流式、会话切换、状态流转）。

**操作**:
1. E2E 已覆盖的 Scenario：抽查确认（不必逐条手测，E2E 门禁已挡低级错误）
2. E2E 覆盖不到的主观项（LLM 报告内容质量、整体体验）：逐条人工验证
3. 记录验证结果到 `tests/validation/YYYY-MM-DD-<change-id>-validation.md`

**模板**:

```markdown
# 人工验证报告: <change-id>

**日期**: YYYY-MM-DD
**验证人**: [姓名]
**关联 delta**: openspec/changes/<change-id>/
**E2E 门禁**: [playwright-report 路径 / 不适用]

## 验证结果

| Scenario | E2E 已覆盖？ | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 会话切换时 SSE 断开重连 | 是（streaming.spec.ts） | 切换后旧连接断开，新会话建立新连接 | 抽查符合 | ✅ |
| 报告内容无明显幻觉 | 否 | 关键财务数据与来源一致 | 逐条核对符合 | ✅ |

## 异常记录
（如有失败项，记录复现步骤和实际行为）

## 结论
[ ] 全部通过，可 archive
[ ] 存在失败项，需修复后重新验证
```

> 人工验证的定位：E2E 看人会漏的（交互状态机、流式生命周期），人看机器看不了的（LLM 输出质量、整体体验）。两者是互补，不是重复。

---

### Step 6: sync + archive

**前置条件（硬关卡，全部满足才能 archive）**:

```
□ openspec/changes/<change-id>/tasks.md 全部勾选
□ Superpowers verification-before-completion 已通过
□ E2E 门禁通过（交互类变更适用）
□ 人工验证报告已落 tests/validation/
□ openspec validate <change-id> --strict 通过
```

**操作**:

1. **sync**: delta spec 合并进主规范库
   - ADDED 需求 → 追加到 `openspec/specs/<capability>/spec.md`
   - MODIFIED 需求 → 替换现有版本
   - REMOVED 需求 → 从主规范库删除

2. **archive**: 变更目录移入归档
   ```
   openspec/changes/<change-id>/ → openspec/changes/archive/YYYY-MM-DD-<change-id>/
   ```

3. **验证**: `openspec validate --strict` 确认归档后状态正确

> archive 是「合并已验证的事实」，不是「归档提案」。未验证就 archive = 把意图当真相写入基线。

---

## 4. Bug 修复流程

### 4.1 A 类: 意图不变（spec 对、代码错）

**判别**: 打开 `openspec/specs/` 对应条目，「正确行为」已写明，但代码没做到。

**流程**:

```
superpowers:systematic-debugging
  │
  ├─ Phase 1: 根因调查
  │   读错误信息 → 稳定复现 → 检查最近变更 → 多组件系统加诊断 → 追踪数据流
  │
  ├─ Phase 2: 模式分析
  │   找正常工作的类似代码 → 对比差异 → 理解依赖
  │
  ├─ Phase 3: 假设与测试
  │   形成单一假设 → 最小变更测试 → 验证
  │
  └─ Phase 4: 实现
      创建失败测试（红）→ 实现单一修复（绿）→ 验证
      如果 3+ 次修复失败 → 停下，质疑架构，与人讨论
```

**红线**:
- 先写复现测试（红），再修 bug（绿）
- 一次只改一个变量
- 不做"顺手改"的额外修改
- 修复后运行 verification-before-completion

**Commit 格式**: `fix: [模块名] 修复xxx问题 (#issue编号)`

**交互类 bug 补充**: 复现测试是 E2E 层级时，spec 代码同样受 §5.5 红线和 §5.6 工具链约束（先 scan.sh 再 e2e-reviewer）；诊断优先用 playwright-debugger，与 systematic-debugging 的 4 阶段互补——debugger 定位浏览器层根因，systematic-debugging 负责跨组件数据流。

### 4.2 B 类: 意图变更 / 行为未定义

**判别**: `openspec/specs/` 翻不到对应条目，或条目本身需要修改。

**流程**: 同新功能（§3 ①→⑥ 完整管线）。delta spec 先定义「应该是什么样」，再实现。

---

## 5. 产物模板与格式

### 5.1 delta spec 模板

**文件位置**: `openspec/changes/<change-id>/specs/<capability>/spec.md`

```markdown
# Delta for <Capability>

## ADDED Requirements

### Requirement: <需求名称>
The system MUST <行为描述>.

#### Scenario: <场景名称>
- GIVEN <前置条件>
- WHEN <触发动作>
- THEN <预期结果>
- AND <附加断言>（可选，可多行）

#### Scenario: <另一个场景>
- GIVEN ...
- WHEN ...
- THEN ...

## MODIFIED Requirements

### Requirement: <已有需求名称>
<新的行为描述>.
(Previously: <旧的行为描述>)

#### Scenario: <场景名称>
- GIVEN ...
- WHEN ...
- THEN ...

## REMOVED Requirements

### Requirement: <已有需求名称>
<废弃原因说明>.
```

**语法要点**:

| 元素 | 规则 |
|---|---|
| Requirement 动词 | `MUST`（强制无例外）/ `SHALL`（强制，实现灵活）/ `SHOULD`（推荐） |
| Scenario 标题 | 必须用 4 个 `#`（`####`），用 3 个会静默失败 |
| GIVEN/WHEN/THEN | 结构化格式，不是装饰。THEN 的断言内容会和测试代码做语义对照 |
| MODIFIED | 必须包含 `Previously:` 说明旧行为，必须包含完整更新内容（不是 diff） |
| REMOVED | 必须说明废弃原因 |

### 5.2 tasks.md 模板

**文件位置**: `openspec/changes/<change-id>/tasks.md`

```markdown
# Tasks: <change-id>

- [ ] 验收项 1（粗粒度，如「SSE 流式中断恢复可用」）
- [ ] 验收项 2（如「会话切换时旧连接正确断开」）
- [ ] E2E spec 已覆盖核心交互场景（仅交互类变更）
- [ ] 验收项 4（如「人工验证报告已落 tests/validation/」）
```

> 执行中回填勾选。archive 前必须全勾。这是 delta 契约与 archive 关卡之间的桥梁。

### 5.3 implementation plan 格式

见 [§3 Step 2](#step-2-writing-plans--拆执行计划)。

**保存位置**: `docs/superpowers/plans/YYYY-MM-DD-<feature-name>.md`

> 计划是任务级耗材，做完即弃，不回填。详细的 TDD 步骤只在计划里，不进 tasks.md。

### 5.4 progress ledger

**文件位置**: `.superpowers/sdd/progress.md`（gitignored）

```markdown
# SDD Progress Ledger

Task 1: complete (commits abc1234..def5678, review clean)
Task 2: complete (commits def5678..ghi9012, review clean after 1 fix cycle)
Task 3: in progress
```

> 对话记忆在上下文压缩后不存活。ledger 是恢复地图——压缩后信任 ledger 和 git log，不信自己的回忆。

### 5.5 E2E spec 写作红线

写 E2E spec 任务卡（§3 Step 2）和审查 spec 代码（§3 Step 3）时的检查清单，由 scan.sh + e2e-reviewer 自动把关：

```markdown
- ❌ 禁止恒真断言：`toBeDefined()` / `assert locator is not None`（P0）
- ❌ 禁止 expect 缺 await（P0）
- ❌ 禁止手动取值后断言（P1，用 web-first assertion 自动重试）
- ❌ 禁止条件绕过：`if (await x.isVisible()) { 断言 }`（P1）
- ❌ 禁止 `waitForTimeout` 赌时序（用 expect 自动重试或等具体事件）
- ❌ 禁止 route.fulfill / MSW 伪造业务接口响应（AGENTS.md 红线；LLM/第三方 `TESTING=1` stub 除外）
- ✅ selector 必须来自真实浏览器快照（data-testid / role+name 优先）
- ✅ 断言稳定的终态，不断言过渡过程
- ✅ 颜色/禁用态用 `toHaveCSS` 断言计算后样式，靠自动重试等 transition 完成
- ✅ 故障注入用 `route.abort()`，合法且推荐
```

**核心原则**（红线背后的判断逻辑，遇到清单没覆盖的场景按此裁决）:

1. 断言「稳定的可观察状态」，不断言「瞬时的过程」
2. 只用 web-first assertion（自动重试），禁止手动取值断言
3. 先探索后编码：任何 selector 必须来自真实浏览器快照，禁止盲写
4. 功能坏了测试必须红——所有「条件绕过」式防御代码都是反模式

### 5.6 E2E 基础设施与工具链

**定位**: e2e-skills 工具链是工作流中浏览器交互层测试的唯一纪律——spec 的生成、审查、诊断分别由 `playwright-test-generator`、`scan.sh` + `e2e-reviewer`、`playwright-debugger` 承担，嵌入 Superpowers 管线的 §3 Step 2 / Step 3 / Step 4.5。单元与集成测试仍走 TDD 五步纪律（§3 Step 2 计划模板），e2e-skills 只接管浏览器层。

**目录结构**（E2E 为独立 TS 项目，与前后端语言解耦）:

```
finance_analysis_agent/
├── backend/                 # FastAPI（不动）
├── frontend/                # React（不动）
└── e2e/                     # 独立 TS Playwright 项目
    ├── package.json
    ├── playwright.config.ts
    ├── .auth/               # 登录态 storageState（gitignore）
    ├── fixtures/
    │   └── seed.ts          # 测试数据准备（request 上下文直接调 API）
    ├── tests/
    │   ├── smoke.spec.ts        # 冒烟：页面可达
    │   ├── streaming.spec.ts    # 流式分析核心链路
    │   ├── contract.spec.ts     # 前后端网络契约
    │   └── interaction.spec.ts  # 交互状态（loading/颜色/禁用态）
    └── playwright-report/   # 失败证据（gitignore）
```

**工具链纪律**（执行时必须遵守）:

| 阶段 | 工具 | 输入 | 输出 |
|---|---|---|---|
| 生成 | playwright-test-generator | Playwright MCP/CLI 真实探索的 DOM 快照 | 失败的 spec |
| 审查 | scan.sh + e2e-reviewer | spec 代码 | P0/P1 反模式报告 |
| 诊断 | playwright-debugger | playwright-report trace/截图 | 根因诊断报告 |
| CI | stub 套件（门禁）+ @live 套件（nightly） | 全量 spec | 通过/失败报告 |

**工具链安装**:

```bash
npx skills add voidmatcha/e2e-skills -g --all
```

**日常流程**:

```
新功能开发
  → playwright-test-generator 生成测试（配合 Playwright MCP/CLI 真实探索）
  → 人工审批场景方案
  → scan.sh 快扫（零 token 抓 P0）
  → e2e-reviewer 深审（24 反模式）
  → 提交 + CI 回归
  → 失败 → playwright-debugger（15 根因诊断）→ 修复后重新回归
```

**落地路线**（一次性建设，P1–P4 完成后门禁生效）:

| 阶段 | 内容 | 验收标准 |
|---|---|---|
| P1 骨架（半天） | e2e/ 初始化、双 webServer、/health 端点、TESTING 开关、冒烟 spec | `npx playwright test` 全链路拉起并跑通 smoke |
| P2 核心链路（1 天） | LLM stub、streaming.spec 三个场景（正常/中断/异常）、contract.spec | 流式行为被确定性验证；断开后端，测试变红 |
| P3 质量工具链（半天） | 装 e2e-skills、跑 scan + reviewer、AGENTS.md 规范落地 | 存量测试 P0 清零 |
| P4 CI（半天） | GitHub Actions、trace artifact、@live nightly | PR 上自动跑，失败可查证据 |
| P5 扩展 | interaction.spec 补交互态、generator 补覆盖率缺口 | 核心用户路径全覆盖 |

> **实现细节**（初始化命令、playwright.config.ts 完整配置、TESTING=1 后端实现、CI yaml 模板、seed/reset 接口设计）见独立文档 [docs/design/E2E测试实现方案-finance_analysis_agent.md](design/E2E测试实现方案-finance_analysis_agent.md)。

---

## 6. 并行变更规则

- 不同 requirement 的提案可并行推进
- 同一 requirement 被两个提案同时 MODIFIED 时，后到者必须 rebase 到先到者的合并结果上
- sync 后主规范库是唯一真相，以它为准解决冲突