"""模型价格配置 → Langfuse 模型价格定义（成本可观测性的唯一入口）。

背景（2026-10-22 观测项）：Langfuse 的 generation 已带 ``usage_details``（token
用量），但 ``totalCost`` 恒为 0——Langfuse 自带 50 个模型定义里没有
``deepseek-v4.1-flash`` / ``glm-5.3-flash``，没有价格就算不出成本。

本模块只做**配置解析与载荷生成**，不发网络请求；写入由
``scripts/register_langfuse_model_prices.py`` 执行。价格未配置时返回空列表，
MUST NOT 编造默认价（错误成本比无成本更有害）。

配置格式（``LLM_MODEL_PRICES``，环境变量，JSON；单位：**美元 / 百万 token**）::

    {"deepseek-v4.1-flash": {"input": 0.27, "output": 1.10},
     "glm-5.3-flash":       {"input": 0.10, "output": 0.30}}

键可写模型短名；注册到 Langfuse 时用 ``(?i)^(.*/)?<name>$`` 前缀可选匹配，
让 ``openai/deepseek-v4.1-flash`` 也能命中。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

_PRICE_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


@dataclass(frozen=True)
class ModelPrice:
    """单模型价格（美元 / 百万 token）。"""

    name: str
    input_per_mtok: float
    output_per_mtok: float

    def as_langfuse_payload(self) -> dict:
        """Langfuse ``POST /api/public/models`` 载荷。

        Langfuse 的 ``unit="TOKENS"`` 价格是**每 token** 单价，故除以 1e6；
        ``matchPattern`` 兼容 ``openai/`` ``anthropic/`` 等前缀。
        """
        return {
            "modelName": self.name,
            "matchPattern": rf"(?i)^(.*/)?{re.escape(self.name)}$",
            "unit": "TOKENS",
            "inputPrice": self.input_per_mtok / 1_000_000,
            "outputPrice": self.output_per_mtok / 1_000_000,
        }


def parse_prices(raw: str | None) -> list[ModelPrice]:
    """解析 ``LLM_MODEL_PRICES`` JSON；空/非法 → 空列表（不抛、不猜）。"""
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    out: list[ModelPrice] = []
    for name, spec in data.items():
        if not isinstance(name, str) or not _PRICE_KEY_RE.match(name):
            continue
        if not isinstance(spec, dict):
            continue
        try:
            inp = float(spec.get("input"))
            outp = float(spec.get("output"))
        except (TypeError, ValueError):
            continue
        if inp < 0 or outp < 0 or not (inp or outp):
            continue
        out.append(ModelPrice(name=name, input_per_mtok=inp, output_per_mtok=outp))
    return sorted(out, key=lambda p: p.name)


def load_prices(env: dict[str, str] | None = None) -> list[ModelPrice]:
    """从环境变量读取价格表（调用时读，便于测试与热更新）。"""
    source = env if env is not None else os.environ
    return parse_prices(source.get("LLM_MODEL_PRICES"))


def cost_details(model: str | None, usage: dict | None) -> dict[str, float] | None:
    """按 token 用量算成本（美元）；未知模型/无价格 → None（不编造）。"""
    if not model or not usage:
        return None
    prices = load_prices()
    if not prices:
        return None
    hit = next((p for p in prices if model == p.name or model.endswith("/" + p.name)), None)
    if hit is None:
        return None
    inp = float(usage.get("input") or 0)
    outp = float(usage.get("output") or 0)
    if inp <= 0 and outp <= 0:
        return None
    cost_in = inp / 1_000_000 * hit.input_per_mtok
    cost_out = outp / 1_000_000 * hit.output_per_mtok
    return {
        "input": round(cost_in, 8),
        "output": round(cost_out, 8),
        "total": round(cost_in + cost_out, 8),
    }
