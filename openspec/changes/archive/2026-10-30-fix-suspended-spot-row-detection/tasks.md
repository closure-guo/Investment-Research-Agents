# Tasks: fix-suspended-spot-row-detection

- [x] 失败测试先行：零 OHLC 形态判停牌 / 无昨行跳过 / 正常行不误判 / 既有 NaN 形态回归（tests/data/test_market_snapshot.py）
- [x] `build_snapshot_rows` 增加第二停牌判据（`_is_zero_trade_row` 纯函数），实现转绿
- [x] 生产存疑行复核：601326 形态的票不再进入 L2 Top-100 / 精选池
      （10-29/10-30 两表均 0，前值 10-26~28 l2 各 2 行、10-28 池 2 行；见验证报告）
- [x] 当日快照重跑后 `open=0 AND high=0 AND low=0 AND close_raw>0 AND is_suspended=0` 计数为 0
      （10-29/10-30 均 0；形态行改为 OHLC 归一 + is_suspended=1，601326 自 10-29 标记落位）
- [x] 既有 D2 合成停牌行（`is_suspended=1` 承接昨收）行为不回归
