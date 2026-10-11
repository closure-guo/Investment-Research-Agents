# Tasks: complete-peer-pipeline

- [x] `fetch_industry_constituents(industry_name)`：东财行业板块成分股（代码+名称），走 `_call_ak` 既有包装，失败/空返回 None + 单测 —— 4 条（解析排序/24h 缓存/失败不写缓存/空表）
- [x] `_fetch_peers` 自动选取：无显式 `peer_codes` 时行业成分按总市值 Top5 排除自身；成分失败/行业缺失/不足 1 只 → None 降级 + 单测（含显式列表恒优先）—— 既有守卫测试按收窄契约同步更新（「不抓取」收窄为自动选取不可得情形）
- [x] `validation_warnings` 非空注入基本面/宏观分析师 context（机生材料段）+ 空 不注入 + 单测 —— 3 条（原文逐字/空缺省/跳过告警）
- [x] 定向测试（tests/data + tests/nodes）+ 全量套件 + ruff 绿
- [x] 真实数据验证：实拉一个行业成分接口 + 601066 无 peer_codes 跑 fetch 层确认自动选取生效 —— 本机东财域 IP 被封（09-26 起，见 memory）：实拉验证走降级路径（RemoteDisconnected×3→None 22.4s，fetch 层 601066 无 peer_codes 23.0s None 不炸管线，符合契约）；happy path 解析排序/缓存由 fixture 单测钉住，真实 happy path 待解封后或 CI 环境（IP 干净）自然命中
- [ ] 人工验证报告落 `tests/validation/`（同业段在无显式对标股报告中呈现）—— 待合并部署后跑无对标股分析补验证
