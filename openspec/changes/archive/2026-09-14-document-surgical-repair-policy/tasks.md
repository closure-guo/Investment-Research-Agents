## 1. 规范

- [x] 1.1 写 delta：`specs/citation-verification/spec.md` 新增 requirement「单点修复的自动处置授权与边界」（三条护栏 + 稀疏阈值 + 失败放行语义 + 变更纪律 + 3 场景）
- [x] 1.2 `openspec validate --strict document-surgical-repair-policy` 通过

## 2. 落地与收口

- [x] 2.1 实现对照：逐条核对护栏在代码中成立（阈值 <3；`citation_analyst_true_fail = 残余 FAIL + repaired`；回填后 `verify_claims` 整体重校验；修复 prompt「禁止引入新数字」；trace `surgical_repairs` 留痕）——结论落验证记录
- [x] 2.2 archive + spec sync（本 delta 无代码变更，验证记录落 `tests/validation/`）
