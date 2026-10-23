# incident 048：生产 Langfuse trader prompt 来自未合并分支，与 main 代码契约错位 9 天

## 现象

2026-10-22 07:26 跑 hosted 全量评估（round14）时被 `prompt-deploy-consistency` 门禁拒绝：

```
trader prompt 与 Langfuse 当前版本不一致，拒绝运行实验（不一致版本：trader）
```

对比 Langfuse `trader` production 版本（v33，`updatedAt 2026-10-12T13:14Z`）与 git main 的
`src/finance_agent/prompts/trader.md`：

- Langfuse 版含「## 受众可执行性约束」整段（按 `受众持仓` 三态决定 sell_type=exit/watch），
  且「未申报 sell_type → 默认按 exit 处理」；
- main 版没有该段，且「未申报 sell_type → 默认按 short 处理」。

该内容来自分支 `add-holdings-context` 的提交 `26218d49`（2026-10-12 21:14，T3），
该分支**从未合并进 main**（领先 11 提交、落后 main 104 提交），但 prompt 已于当晚被部署到
共享 Langfuse production。

## 影响

1. 生产自 2026-10-12 起运行「main 代码 + 分支 prompt」：分支 prompt 要求 trader 读取
   `受众持仓: ...` 上下文（main 代码不注入）、并期待缺省 sell_type 走 exit（main 代码默认
   short）——决策层 prompt/代码契约错位，至少造成口径漂移，可能放大价位/动作相关打回。
2. `prompt-deploy-consistency` 门禁在评估时才暴露，日常分析与日批不检查，漂移可长期潜伏。
3. 漂移内容不在 main 提交历史中，部署脚本的历史匹配无法识别，只能按「UI 编辑未收编」拒绝，
   阻塞评估。

## 处置（2026-10-22）

- 确认 Langfuse v33 内容 == 分支 `26218d49` 的 `trader.md`（逐行 diff），不是 UI 手改；
- 以 git 权威口径，将 main 版 `trader.md` 强制发布为 production：
  `uv run python scripts/deploy_prompts.py --force --exclude <其余 20 个>` → `OK trader (3284 chars)`；
- 分支内容仍在 git（`add-holdings-context`），待 rebase + review 合并后再按流程发布；
- 评估门禁随后放行，round14 正常启动。

## 根因

- `scripts/deploy_prompts.py` 从**工作树**发布，不校验当前分支是否 main：任何人在 feature
  分支上执行一次部署就会污染共享 Langfuse（ADR-0016 的「git 是唯一权威源」在部署入口没有
  机械保障）。
- 一致性门禁只在 eval 启动时比对，日常链路无检查点。

## 防复发（待落）

1. `deploy_prompts.py` 增加分支守卫：当前分支非 main 且非 `--allow-branch` 时拒绝发布（或以
   独立 label 发布，不碰 production）。
2. 每日 `integrity_check`（或独立 job）增加 prompt 一致性检查，漂移即 failed 进 `/ops`。
3. 分支验证需要独立 Langfuse label/项目，禁止直接写 production。

## 关联

- 分支：`add-holdings-context`（11 commits，含验证报告 `a0e6d3dd`，未合并）
- 本轮处置的旁证：Windows 下 `git show` 输出 UTF-8、Python 按 GBK 解码导致
  `deploy_prompts._head_contents` 返回 None（脚本未指定 `encoding="utf-8"`）；本次以
  `PYTHONUTF8=1` 绕过，脚本健壮性修复另记。
