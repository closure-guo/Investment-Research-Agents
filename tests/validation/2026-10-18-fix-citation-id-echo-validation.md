# 人工验证报告: fix-citation-id-echo（issue #327 后续项 2）

**日期**: 2026-10-18
**验证人**: ZCode 验证 runner（单测对照 + 反掩蔽控制）
**关联 issue**: #327（add-decision-memory）后续项 2——citation 回声校验对「来源 ID 数字」的识别
**E2E 门禁**: 不适用（引用校验器契约项，纯后端）
**执行环境**: worktree `.worktrees/fix-citation-id-echo` @ 基于 main 86cdbead

## 缺口与修复

**缺口**（issue #327 原文，E2E 实测已知交互）：记忆块要求 interpretation 不写来源 ID；若 LLM 照写（如「历史决策 p_2f2d5230155c 结算收益为正」），ID 内数字被 `_extract_number_candidates` 读作数值候选 → `_check_internal_echo` 判 `internal_inconsistency` **误报**。此前仅有块内指令缓解，未根治。

**修复**（`citation.py`）：新增 `_SOURCE_ID_PATTERN`——`p_<≥6 hex>` / `c_<≥6 hex>`（prediction_id / clause_id 两族，均为 `前缀 + uuid4().hex[:12]`）——回声校验前将 ID 记号置换为空格后再提取数值候选。词界按非 ASCII 字母数字判定（紧邻 CJK 无空格也命中）；ID 之外的独立数值不受影响。

## 验证结果

| # | 场景 | 证据 | 通过 |
|---|---|---|---|
| 1 | `p_<hex>` 在场 → 不再误报 | 复现用例重写为真实 ID 形态：with_id → **PASS**（修复前红：internal_inconsistency） | ✅ |
| 2 | `c_<hex>`（条款 ID）同族豁免 | 同用例追加 clause-id 分支 | ✅ |
| 3 | **反掩蔽控制**：ID 之外独立数值仍参与回声 | 「…p_2f2d5230155c 结算收益 2.5% 为正」（stated 0.181）仍判 internal_inconsistency | ✅ |
| 4 | 不写 ID 行为不变 | 对照分支 PASS | ✅ |
| 5 | 回归面 | `tests/memory/` + `tests/test_citation*.py` + `tests/nodes` 合计 **954 项绿**；ruff/mypy 零告警 | ✅ |

## 未达成项 / 披露

1. 豁免判据为**形态式**（`[pc]_ + ≥6 hex`）；生产 ID 均为 `uuid4().hex[:12]`（12 位），形如 `p_own_2` 的短/非 hex 手写 token 不在豁免面——测试夹具已改为真实形态；如未来出现其它 ID 前缀家族（如 `s_`），按同模式扩展。
2. 本项为 issue #327 的「后续项 2」；#327 主项（n=10 消融批次）与后续项 3（月批接线）另行处置：项 3 经核查**已由后续 delta 完成**（`memory_extract_monthly` 已注册进 job 家族（add-monthly-batch-scheduling）+ 确认 API 与 `/memory` 工作台已交付（add-memory-confirmation））。
