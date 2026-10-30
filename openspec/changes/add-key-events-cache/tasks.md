# Tasks: add-key-events-cache

- [ ] 缓存语义四分支单测：同日二次调用零网络命中；验证过的空被缓存；失败兜底不缓存；`use_web_search=False` 不读不写
- [ ] 白名单单测：新增 6 域命中（cls.cn/xueqiu.com/news.qq.com/investing.com/yicai.com/nbd.com.cn）；行情页域（cfi.cn/9fzt.com）仍被滤除
- [ ] 传名单测：装配与时效校验的检索查询含股票名；缺名降级不阻断批次
- [ ] 消费方零改动回归：triage prompt 事件渲染、时效校验、深析 fetch 节点定向测试全绿（返回形状不变）
- [ ] `uv run ruff check` + `uv run mypy`（存量基线内）+ 定向 pytest 通过
- [ ] 部署后生产读数验证落 `tests/validation/`：15:40 批事件装配耗时 + 次日池 L3 理由「事件数据不可用」占比对比（基线 31/35 ≈ 89%）
