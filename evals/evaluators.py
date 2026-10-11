"""确定性评估器:零 token、可重算、可进 CI(spec Requirement「确定性评估器」)。"""

from __future__ import annotations

from evals.sections import find_section


def section_coverage(report: str | None, expected_output: dict) -> dict | None:
    """必备章节覆盖率。expected 无 must_cover 时返回 None(不计入该维度)。

    must_cover 先去重（#55）:重复的缺失项不得虚增分母压低覆盖率,缺失清单
    亦不得重复罗列。
    """
    must_cover = list(dict.fromkeys(expected_output.get("must_cover") or []))
    if not must_cover:
        return None
    if report is None:
        return {"name": "section_coverage", "value": 0.0, "comment": "无报告产出"}
    missing = [s for s in must_cover if not find_section(s, report)]
    value = (len(must_cover) - len(missing)) / len(must_cover)
    return {
        "name": "section_coverage",
        "value": round(value, 4),
        "comment": f"缺失章节: {', '.join(missing)}" if missing else None,
    }


_TICKER_SUFFIXES = (".SH", ".SZ", ".SS", ".BJ")


def _normalize_ticker(ticker: str) -> str:
    """比较归一（#55）:去首尾空白、大写、剥交易所后缀——后缀变体不误判 0 分。"""
    t = ticker.strip().upper()
    for suffix in _TICKER_SUFFIXES:
        if t.endswith(suffix):
            return t[: -len(suffix)]
    return t


def ticker_match(ticker: str | None, expected_output: dict) -> dict | None:
    """标的解析正确性。expected 无 ticker 时返回 None。比较经 _normalize_ticker 归一。

    已知局限（#55 文档化）:deep 路径 output.ticker 回显 input.stock_code
    （管线无标的解析节点,initial_state 直注 stock_code）——deep 维度退化为
    vacuous,仅「expected 与回显不一致」与 quick 的 None→0.0 可被捕获;真正的
    解析正确性需 query-only 模式（initial_state 不给 stock_code,依赖尚不存在
    的解析节点）,留待立项。归一化使后缀/大小写变体不再误判。
    """
    expected_ticker = expected_output.get("ticker")
    if not expected_ticker:
        return None
    if ticker is None:
        return {"name": "ticker_match", "value": 0.0, "comment": "未解析出标的"}
    matched = _normalize_ticker(ticker) == _normalize_ticker(str(expected_ticker))
    return {
        "name": "ticker_match",
        "value": 1.0 if matched else 0.0,
        "comment": None if matched else f"期望 {expected_ticker},实际 {ticker}",
    }


def make_evaluation(result: dict):
    """评估结果 dict → langfuse Evaluation(langfuse 4.13 experiment API)。

    value 为 float;comment 可为 None。Evaluator 仅经 run_experiment 调用;
    langfuse 不可用时 run.py 在进入实验前即显式报错退出。
    """
    from langfuse.experiment import Evaluation

    return Evaluation(name=result["name"], value=result["value"], comment=result.get("comment"))
