"""执行末端 broker 抽象与实现（add-paper-broker）。

- ``base.py``：``Broker`` 抽象接口与共享值类型 ``OrderRecord``（spec
  broker-execution「Broker 抽象接口」）；
- ``paper.py``：``PaperBroker`` 仿真实现（经 matching-engine 撮合；
  orders/positions/cash/paper_equity 落 execution 库）。
"""
