# 部署取证报告: feed-dsl-contract-into-proposal-prompt（#510 distro 补丁）

**日期**: 2026-10-31
**验证人**: agent（distro 四重取证 + 渲染探针）
**关联 delta**: openspec/changes/feed-dsl-contract-into-proposal-prompt/（代码 PR #510）
**性质**: 部署已在位的事后取证（任务 9 回填）——补丁实为 **2026-10-29 00:01** 应用
（10-28 晚宕机排障重建会话所为，未回填任务导致记录过时），本报告补齐验证证据。

## 四重取证

| # | 取证项 | 实测 |
|---|---|---|
| 1 | distro 目标 mtime | `/opt/rdagent/.../proposal/factor_proposal.py` = **2026-10-29 00:01:03** |
| 2 | 备份在位 | `factor_proposal.py.bak-20261028-contract` 存在（内容为 10-22 原始形态） |
| 3 | 幂等脚本复跑 | `patch-prompt-contract.py` 返回 "already patched; nothing to do"；目标文件 combined import（`render_contract_block, render_explored_block`）✓ / NEW 块（`_spec_text` 前缀注入）✓ / OLD 锚点残余 ✗ |
| 4 | 编译 | `py_compile` 通过 |

## 渲染探针（prompt 实际含契约段的充分条件）

distro venv 内实跑 `render_contract_block()`：**非空，846 字符**，头部
`# Hard contract for factor expressions (BINDING, checked BEFORE evaluation)`——
数据源（`explored_factors.json` contract 块，10-30 16:11 生产载荷实读在场）与
渲染函数（补丁单测 + I1 算子全量列出审查）均已验证；NEW 块将契约段前置到
`hypothesis_specification`，故**下一个 RD-Agent 提议会话进程起 prompt 必含契约段**
（模块进程启动时导入）。首个真实会话 prompt 的落日志复核随任务 10 的批次读数
一并核（fail-safe：源缺失→空串→注入跳过，不阻断）。

## 结论

- [x] 任务 9（部署到 distro site-packages + 探针验证）证据齐备，回填勾选
- [ ] 任务 10（3-5 个收割批次读数对照）：已过 2 个全程暴露批（10-29/10-30，n=329），
      继续收集；第 3 批后无信号按预登记降级（metrics.md §2 A2 行修正版判读）
- [ ] 任务 11（人工验证报告 → sync + archive）：随任务 10 收口
