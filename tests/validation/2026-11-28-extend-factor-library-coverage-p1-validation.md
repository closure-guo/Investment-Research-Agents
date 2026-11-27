# P1 验证报告：extend-factor-library-coverage（离线确定性复译测量）

**日期**：2026-11-28
**验证人**：agent（执行者），待 owner 复核
**关联 delta**：`openspec/changes/extend-factor-library-coverage/`（P1 = §1–§5）
**E2E 门禁**：不适用（无前端 / SSE / 会话流转变更）

## 一句话结论

P1 代码全部落地且测试全绿；对生产收割状态文件做**离线确定性复译测量**——存量 1,772 条不可译中携带 qlib 表达式的 836 条里，**405 条（48.4%）现在可翻译并过复杂度**（原估算「约 398 条」复核成立、实测略高）；剩余 431 条全部死在复杂度三轴（深度 229 / 自由参数 121 / 字段种类 75 / 其他 6），与「Qlib 公式写法冗长」一致，属后续中间表示改造（B1）的范畴。

## 方法

- 脚本：`tests/scripts/measure_dsl_unlock.py`（只读状态文件；零 LLM、零生产写入）。
- 输入：生产 `data/factor_research/harvest_state.json`（容器内只读拷出；`updated_at=2026-10-30T16:11`，其后供给停机无新增）。
- 口径：对每条 `status=untranslatable` 且已记录 `qlib_expression` 的候选，用新翻译器重译；`translate_qlib_expression` 内部已含 DSL 复杂度校验（可翻译 **且** 过复杂度才算解锁）。
- **为什么不直接跑生产重放**：`scripts/replay_factor_harvest.py` 对每条候选重新调用 LLM 转译（`_transcribe_with_retry` 无复用已记录 qlib 表达式的分支），834 条即 834 次 LLM 调用；且 LLM 非确定性会让「DSL 扩展」这一单变量与转译噪声混在一起。Prod 重放另行确认（见下「未执行」）。

## 读数（`[实测]` 2026-11-28）

| 指标 | 数值 |
|---|---|
| untranslatable 总数 | 1,772 |
| 其中携带 qlib 表达式 | 836 |
| **解锁（可翻译 + 过复杂度）** | **405（48.4%）** |
| 仍被挡：复杂度·深度 | 229 |
| 仍被挡：复杂度·自由参数 | 121 |
| 仍被挡：复杂度·字段种类 | 75 |
| 仍被挡：翻译其他 | 6 |
| 无 qlib 表达式（llm 阶段拒译，不参与本测量） | 936 |

对照基线（delta 设计期的正则近似估计）：814 条复杂度拒译中约 398 条可被去重口径解锁——本次用真实翻译器复验为 **405**，同量级且略高。

解锁样例（前若干条，完整清单可由脚本 `--json` 重放）：

- `ABNORMAL_SIGNED_VOLUME_SHARE_20`：符号量加权成交份额（`div(ts_sum(mul(sign(ret),volume/mean20),20),ts_sum(volume/mean20,20))`）
- `ABNORMAL_VOLUME_RETURN_SLOPE_20`：量价协同斜率（`div(ts_sum(mul(volratio−1,ret),20),add(ts_sum(mul(volratio−1,volratio−1),20),1e-8))`）
- `ACCEL_5`：动量二阶差分（`sub(sub(div(close,ref(close,5)),1),sub(div(ref(close,5),ref(close,10)),1))`）
- `ADAPT_VOL_CONF_SIGN_REV_5_20`：自适应波动确认符号反转（`mul` + `ts_corr` 组合）

## 代码门禁

- `uv run pytest tests/factors`：**630 passed**（P1 新增 61 条测试；含新算子语义、翻译器映射回归、性能预算护栏）。
- `uv run pytest tests/scripts/test_measure_dsl_unlock.py`：1 passed。
- 全量 `uv run pytest`：见下方「全量结果」。
- `uv run ruff check` / `ruff format --check` / `uv run mypy`（改动源文件）：全绿。
- `openspec validate extend-factor-library-coverage --strict`：通过。

## 未执行 / 未验证

1. **生产重放未执行**（delta 13.1 的写库部分）：需 834 次 LLM 转译（成本 + 非确定性），且会改写生产收割状态与因子库（可能产生新入库）。等 owner 确认后另做；届时建议用「离线测量的 405 条解锁清单」作为预期上限对照。
2. **新建 DSL 能力在真实面板上的表现**：`ts_resid`/`cs_mean`/`ind_neutralize` 等只有单元测试与合成面板验证；真实 500 日面板上的数值行为（如 `ind_neutralize` 行业标签覆盖率）在 P2（参考数据面）后才可测。
3. **P2/P3 未实施**：参考数据面、builtin 5→7、选股缺口披露、提案枚举与模板（tasks §6–§13 未勾）。提示词模板措辞随 P3 一次发布。
4. 存量 12 条 active 因子的表达式与读数按设计「只放宽不收紧」——新口径下全部仍合法（`tests/factors` 回归覆盖），但生产库未做任何写操作。

## 全量结果

（见 PR #629 的 CI 与本地全量 pytest 输出）
