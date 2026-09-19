# Tasks

- [x] 抽出 `_compare_numeric_claim` 共用比对（数值型/计算型统一容差语义）
- [x] 路由判据 `_recomputable_root` + 重算优先 + 输入不可得时显式降级（保留 coverage_gap）
- [x] TDD：numerical 标在注册表根 + 被污染派生值 → FAIL；非注册表根行为不变
- [x] 干净材料误报基线测量（10 标的 596 claim，零新增值级误报）
- [x] 冻结重放前后对比（零 LLM）：修复前 0/5 拦下 → 修复后 2/5 拦下（value_mismatch 桶）
- [x] spec delta 归档（sync 进 citation-verification 主规范库）
