# Tasks: extend-factor-dsl-coverage

> 上线顺序 = 任务顺序。TDD：先红后绿。三条变更联合必要（见 design D1），同批交付。

## T1 DSL：深度 5→6（factors/dsl.py）

- [x] 先红后绿：`MAX_AST_DEPTH == 6`；深度恰 6 通过 / 深度 7 拒收（「深度 7 > 上限 6」逐字）；
      深度 6 自然形态（CLV 平滑 / VWAP 偏离）通过
- [x] 三违规并列、递归转译消息随新上限；文档串同步

## T2 DSL：ts_sum / sqrt（factors/dsl.py + eval_engine.py）

- [x] 先红后绿：枚举 18 逐字；`ts_sum(volume, 5)` / `sqrt(close)` 解析 + round-trip；窗口形态拒收
- [x] 先红后绿：`ts_sum` 滚动求和（min_periods=window、逐标的独立）；`sqrt` 负值 → NaN、0 → 0、
      NaN 传播；复合式 `sqrt(ts_sum(volume,5))`

## T3 翻译器（tests/scripts/spike/qlib_translate.py）

- [x] `Sum` 映射 `missing` → `equivalent`（→ `ts_sum`）；`Power(x, 0.5)` 特例映射 → `sqrt`
      （精确等价；其余指数仍拒）
- [x] 用例：Sum 等价、VWAP 偏离复合式可翻译（深度 6 下）、`Power(·,0.5)` 特例可翻译、
      `Power(·,2)` 仍拒、映射表状态翻转

## T4 prompt 同步 + 发布

- [x] `factor_proposal.md`：算子清单 + `ref`（补记）/`ts_sum`/`sqrt`；复杂度说明「深度 ≤ 6」；发布并在产校验

## T5 复跑与证据（第四轮）

- [x] 批 A（10 条）重跑：翻译（预期 10/10 不变）+ 过闸读数
- [x] 批 B（22 条）重跑：翻译（预期 19/22：clv_5d + VWAP×2 + park_vol 解锁；amihud×2 与
      max_ret_20d 留残余）+ 过闸读数
- [x] 读数落档 → 验证报告 `tests/validation/`（含三条变更的机械归因表）

## 收口

- [x] `uv run pytest tests/factors tests/screening tests/test_api_factors.py tests/test_api_screening.py
      tests/scripts/spike/test_qlib_translate.py -q` 全绿；ruff / mypy 零新增
- [x] `openspec validate extend-factor-dsl-coverage --strict` 通过
- [x] 验证报告 + PR；sync + archive 在验证通过后执行
