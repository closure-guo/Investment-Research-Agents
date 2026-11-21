"""@live 用例统一的 opt-in 门禁（issue #605；纪律出处 #158 / nightly workflow）。

契约：@live 只在**显式 opt-in**（shell/CI 设 ``RUN_LIVE=1``）时运行。

为什么 key 在场不算激活：全量套件中 ``src.finance_agent.api`` import 即
``load_dotenv()``，root .env 的 LANGFUSE key 必然进入 ``os.environ``——纯
key 判据无法区分「用户想跑真链路」与「前序导入带来的凭据泄漏」，后者会让
@live 用例在本地全量跑批时意外触网（#605 实证：fm_decision 单独/全量均被
激活并失败）。nightly workflow（e2e-playwright.yml ``pytest -m live``）已
显式设 ``RUN_LIVE: "1"``；本地手动真链路跑用 ``RUN_LIVE=1 pytest -m live``。

第二道判据仍查 LANGFUSE key：opt-in 已开但未配 key（nightly secrets 缺省）
→ 按既有「无 key 跳过」纪律空跑，不报失败。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def require_live_opt_in() -> bool:
    """@live 双道门禁：先查显式 opt-in，再查 key；不满足即 ``pytest.skip``。"""
    # 第一道：显式 opt-in 开关。凭据在场 ≠ 用户想跑 @live。
    if os.environ.get("RUN_LIVE") != "1":
        pytest.skip("RUN_LIVE 未开启（@live 为显式 opt-in），跳过")
    # 显式开启后允许从项目 .env 补 key（与 run.py _load_env 同源；手动 nightly 用）
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except Exception:  # noqa: BLE001, S110 - dotenv 缺失时跳过
        pass
    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        pytest.skip("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 未设置，跳过 @live 用例")
    return True
