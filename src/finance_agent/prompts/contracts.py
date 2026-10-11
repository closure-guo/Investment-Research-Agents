"""跨 prompt 共享的决策层行为片段（单一来源）。

add-horizon-semantics-to-decision-layer：周期契约段由本模块唯一维护，
trader / risk_judge / fund_manager 三个 prompt 的 md 文件内嵌同一字面文本，
tests/test_prompt_contracts.py 断言三者与本常量逐字一致——改本常量必须
同步改三份 md（测试拦截漂移）。

「20 个交易日」与结算配置 OUTCOME_DEFAULT_HORIZON_DAYS（默认 20）联动，
配置变更时同步更新此处与三份 md。
"""

HORIZON_CONTRACT = """## 周期契约

本决策按 **T+20 个交易日相对沪深300 超额收益（±2% 中性带）** 考核（口径与结算配置
OUTCOME_DEFAULT_HORIZON_DAYS 联动，该配置变更时本段须同步更新）。据此：
- buy 决策 MUST 说明催化剂/兑现路径为何能在 20 个交易日内兑现（如财报披露、解禁落地、
  政策窗口、技术位触发）。
- 兑现期超过 20 个交易日的长线论据（估值回归、产能释放、长期市占率等）MUST NOT 作为
  买入主因，仅可作安全垫（佐证下行风险有限）或否决项（推翻买入）。
- watch/hold/sell 语义不变；watch 的 reeval_triggers 按既有语义给出周期内触发条件。"""
