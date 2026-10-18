"""拥挤度 regime 包（delta add-crowding-regime-positioning）。

单职责小文件：
- ``indicators``：四指标 + 历史分位（纯函数）；
- ``judgment``：regime 判定与陈旧度（纯函数，零 LLM）；
- ``config``：``config/regime.json`` 加载（缺文件回落 + WARN，损坏 fail loud）；
- ``model``：``regime.db`` 存储（``regime_daily`` 幂等 upsert）；
- ``job``：日批编排（快照之后跑批，不产行时留痕原因码）；
- ``payload``：``GET /api/regime`` 只读载荷。
"""
