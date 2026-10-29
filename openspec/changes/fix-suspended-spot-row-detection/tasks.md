# Tasks: fix-suspended-spot-row-detection

- [x] 失败测试先行：零 OHLC 形态判停牌 / 无昨行跳过 / 正常行不误判 / 既有 NaN 形态回归（tests/data/test_market_snapshot.py）
- [x] `build_snapshot_rows` 增加第二停牌判据（`_is_zero_trade_row` 纯函数），实现转绿
- [ ] 生产存疑行复核：601326 形态的票不再进入 L2 Top-100 / 精选池
- [ ] 当日快照重跑后 `open=0 AND high=0 AND low=0 AND close_raw>0 AND is_suspended=0` 计数为 0
- [x] 既有 D2 合成停牌行（`is_suspended=1` 承接昨收）行为不回归
