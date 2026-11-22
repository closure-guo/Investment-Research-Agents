# Tasks: add-settlement-price-caliber

- [x] data/market_caliber.py：settlement_series（hfq fail-closed）/ monitor_series（qfq）/ benchmark_close_on_or_before / normalize_dates，单测先红后绿（合成 client）
- [x] job.py 与 events/journal.py 结算取数收编 settlement_series（口径参数从调用方消失）
- [x] marking.py 收编 monitor_series + judgment/marking 的 helper 拷贝归一
- [x] akshare_client.SETTLEMENT_ADJUST 注释指向 caliber 单一真相源
- [x] 全量回归 + ruff/mypy/format 三连
