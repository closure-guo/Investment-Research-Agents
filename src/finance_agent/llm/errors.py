# src/finance_agent/llm/errors.py
"""LLM Gateway typed errors（设计档案 §8）。

分类即处理路径：可重试的（瞬时服务错误）由调用方决定重试；
不可重试的（鉴权/内容过滤）直接上抛，禁止盲目重试。
"""

from __future__ import annotations


class LLMError(Exception):
    """gateway 错误基类。"""

    retryable: bool = False


class OutputTruncatedError(LLMError):
    """finish_reason=length —— reasoning 吃光配额或输出超预算。

    处理路径：复核 max_tokens 预算（Task 2.3 派生）或 repair 重试。
    """

    retryable = True


class ContentFilteredError(LLMError):
    """finish_reason=content_filter —— 内容被端点过滤，不盲目重试。"""

    retryable = False


class EmptyLLMOutputError(LLMError):
    """无 finish_reason 且无正文 delta —— 模型「思考后即止」（incident 017）。

    处理路径：重试（强化「直接输出正文」指令）。
    """

    retryable = True


class AuthError(LLMError):
    """鉴权失败 —— 检查 api_key，不重试。"""

    retryable = False


class RateLimitError(LLMError):
    """限流 —— 可重试（退避）。"""

    retryable = True


class LLMTimeoutError(LLMError):
    """请求超时 —— 可重试。"""

    retryable = True


class ProviderInternalError(LLMError):
    """provider 5xx / 内部错误 —— 可重试（瞬时故障，退避重试）。"""

    retryable = True


class ModelNotFoundError(LLMError):
    """模型不存在 —— 不重试。"""

    retryable = False


class ContextOverflowError(LLMError):
    """上下文超窗 —— 走截断/摘要/fallback 长窗策略。"""

    retryable = False


class UnsupportedCapabilityError(LLMError):
    """provider 不支持请求的关键参数（tools/tool_choice/response_format 等）。

    处理路径：显式降级（如 action 文本协议）或上抛，禁止静默 drop。
    """

    retryable = False


class OutputContractError(LLMError):
    """结构化输出合同最终失败（extract→validate→repair→fallback 全链）。

    携带 raw_excerpt 供 trace 审计。
    """

    retryable = False

    def __init__(self, message: str, raw_excerpt: str = ""):
        super().__init__(message)
        self.raw_excerpt = raw_excerpt


class UnknownLLMError(LLMError):
    """未分类的 LLM 异常 —— 包装保留上下文，不吞掉。"""

    retryable = False


# 旧名兼容别名（阶段内迁移用，收尾后移除）
OutputTruncated = OutputTruncatedError
ContentFiltered = ContentFilteredError
EmptyLLMOutput = EmptyLLMOutputError
ModelNotFound = ModelNotFoundError
ContextOverflow = ContextOverflowError
