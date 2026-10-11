"""AKShare 连接不可达 fail-fast（issue #246：CI 断网窗口 stub 套件假红治理）。

`_call_ak` 对「Network is unreachable / Connection refused」类错误不再重试：
秒级窗口内不会自愈，重试只产生纯等待与日志噪声。限频类
（RemoteDisconnected）重试语义保持不变。
"""

from __future__ import annotations

from concurrent.futures import TimeoutError as FuturesTimeoutError  # noqa: F401

import requests

from finance_agent.data import akshare_client as ac


def test_unreachable_no_retry_and_no_sleep(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(ac.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def flaky_unreachable(*args, **kwargs):
        calls["n"] += 1
        raise requests.exceptions.ConnectionError("[Errno 101] Network is unreachable")

    result = ac._call_ak(flaky_unreachable)
    assert result is None
    assert calls["n"] == 1, "连接不可达必须首试即放弃，不得重试"
    assert sleeps == [], "不可达路径不得触发线性退避等待"


def test_errno_101_oserror_via_cause_chain(monkeypatch):
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        try:
            raise OSError(101, "Network is unreachable")
        except OSError as orig:
            raise requests.exceptions.ConnectionError("fetch failed") from orig

    assert ac._call_ak(wrapped) is None
    assert calls["n"] == 1


def test_remote_disconnected_still_retries(monkeypatch):
    """限频类（RemoteDisconnected）保持既有重试语义：第二次成功即返回。"""
    sleeps: list[float] = []
    monkeypatch.setattr(ac.time, "sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def flaky_limit(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            import http.client

            raise http.client.RemoteDisconnected("Remote end closed connection")
        return "ok"

    assert ac._call_ak(flaky_limit) == "ok"
    assert calls["n"] == 2
    assert sleeps, "限频重试仍走线性退避"


def test_refused_error_no_retry(monkeypatch):
    monkeypatch.setattr(ac.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def refused(*args, **kwargs):
        calls["n"] += 1
        raise ConnectionRefusedError(111, "Connection refused")

    assert ac._call_ak(refused) is None
    assert calls["n"] == 1
