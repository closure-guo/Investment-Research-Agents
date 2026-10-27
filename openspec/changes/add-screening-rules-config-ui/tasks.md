# Tasks: add-screening-rules-config-ui

- [ ] `screening/rules_config.py`：三项规则 resolve/save/validate（file > env > default、
      原子写、file_status 披露），默认值与既有常量逐位一致（TDD）
- [ ] API 端点 `GET/PUT /api/settings/screening-rules`：200/400 语义与来源披露（TDD）
- [ ] `pipeline.run_screening_batch` 主档接线：批次开始解析配置 → 显式 thresholds + top_n；
      显式 thresholds 参数优先、小票通道与默认路径行为逐位不变（TDD）
- [ ] 前端「选股规则」分区：导航项 + `ScreeningRulesPane`（万元换算、来源徽章、前端拦截、
      保存反馈、生效时机、加载失败隔离）（组件测试）
- [ ] E2E spec 已覆盖核心交互场景（分区入口/保存/非法拦截）
- [ ] 人工验证报告已落 `tests/validation/`
