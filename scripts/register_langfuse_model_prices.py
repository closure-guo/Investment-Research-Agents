"""把 ``LLM_MODEL_PRICES`` 注册为 Langfuse 模型价格定义（成本可观测性）。

用法::

    # 1) 在 .env 配置（美元 / 百万 token）
    LLM_MODEL_PRICES={"deepseek-v4.1-flash": {"input": 0.27, "output": 1.10}}

    # 2) 预演（只打印将创建的模型，不写）
    uv run python scripts/register_langfuse_model_prices.py --dry-run

    # 3) 写入（幂等：已存在的 modelName 跳过）
    uv run python scripts/register_langfuse_model_prices.py --apply

注册后新产生的 trace 由 Langfuse 按 usage_details × 单价自动算成本；
存量 trace 不回溯重算（Langfuse 语义）。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finance_agent.llm.pricing import load_prices  # noqa: E402


def _env(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value.strip()
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip()
    return ""


def _api(method: str, url: str, payload: dict | None, auth: str) -> dict:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(  # noqa: S310 - 固定自托管地址
        url,
        data=body,
        method=method,
        headers={"Authorization": auth, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - 固定自托管地址
        return json.loads(resp.read().decode() or "{}")


def main() -> int:
    parser = argparse.ArgumentParser(description="注册 Langfuse 模型价格定义")
    parser.add_argument("--apply", action="store_true", help="实际写入（默认 dry-run）")
    parser.add_argument("--dry-run", action="store_true", help="只打印（默认行为）")
    args = parser.parse_args()

    prices = load_prices()
    if not prices:
        print("[SKIP] LLM_MODEL_PRICES 未配置或为空——无法注册价格（不编造默认价）")
        return 0

    pk, sk = _env("LANGFUSE_PUBLIC_KEY"), _env("LANGFUSE_SECRET_KEY")
    host = _env("LANGFUSE_HOST") or "http://localhost:3000"
    if not pk or not sk:
        print("[SKIP] 缺少 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY")
        return 0
    auth = "Basic " + base64.b64encode(f"{pk}:{sk}".encode()).decode()

    existing = {
        row.get("modelName")
        for row in _api("GET", f"{host}/api/public/models", None, auth).get("data", [])
    }
    created = 0
    for price in prices:
        payload = price.as_langfuse_payload()
        if price.name in existing:
            print(f"[SKIP] {price.name} 已存在")
            continue
        if not args.apply:
            print(f"[DRY] 将创建 {price.name}: {payload}")
            continue
        _api("POST", f"{host}/api/public/models", payload, auth)
        created += 1
        print(
            f"[OK] {price.name} 已注册（{price.input_per_mtok}/{price.output_per_mtok} USD per 1M）"
        )

    print(f"done: created={created}, prices={len(prices)}, apply={args.apply}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
