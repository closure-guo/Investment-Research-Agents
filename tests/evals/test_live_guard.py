"""@live opt-in 门禁单元测试（issue #605）：凭据在场 ≠ 激活 @live。

#158 纪律的守卫契约（A 类修复：nightly workflow 与 hallucination_live 已遵守，
fm_decision/performance/toolcall 三文件只查 key 且主动 load .env，未遵守）：

1. ``RUN_LIVE != "1"`` → 一律 skip——即使 LANGFUSE key 已在 os.environ
   （全量套件中 src.api import 即 load_dotenv，root .env 的 key 必然进环境，
   纯 key 判据无法区分「想跑」与「泄漏」）；
2. ``RUN_LIVE=1`` 但 key 缺失 → skip（nightly 无 secrets 时按纪律空跑）；
3. ``RUN_LIVE=1`` 且 key 在场 → 放行（真链路手动/nightly 跑）。
"""

from __future__ import annotations

import pytest

from tests.evals._live_guard import require_live_opt_in


def test_skips_when_run_live_absent_even_if_keys_present(monkeypatch):
    """凭据已泄漏进环境（模拟 src.api import 后）但未显式 opt-in → skip。"""
    monkeypatch.setenv("RUN_LIVE", "")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-leaked")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-leaked")

    with pytest.raises(pytest.skip.Exception):
        require_live_opt_in()


def test_skips_when_run_live_on_but_keys_missing(monkeypatch):
    """显式 opt-in 但无 key（nightly 未配 secrets、无 .env）→ skip，不触网。"""
    monkeypatch.setenv("RUN_LIVE", "1")
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    # 密闭环境：本机 root/.env 有真 key，loader 置空以模拟 CI（无 .env 文件）
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)

    with pytest.raises(pytest.skip.Exception):
        require_live_opt_in()


def test_passes_when_run_live_and_keys_present(monkeypatch):
    """显式 opt-in + key 在场 → 放行（loader 置空，不触真 .env/网络）。"""
    monkeypatch.setenv("RUN_LIVE", "1")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)

    assert require_live_opt_in() is True
