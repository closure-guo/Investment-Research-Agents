"""选股评估包（add-screening-evaluation）：候选池前向跟踪、消融变体、分层考核与报告。

全部为**纯计算 + 注入数据**（面板 / screening 库路径 / 因子库路径显式传参），
零网络、零 LLM；实时数据受快照链路（issue #325）阻塞，live cadence 为后续项。
"""
