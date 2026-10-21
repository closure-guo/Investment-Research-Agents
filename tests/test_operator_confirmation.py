"""写操作确认令牌（add-quick-mode-operator P3）。"""

from __future__ import annotations

import pytest

from finance_agent.operator.confirmation import (
    TOKEN_TTL_SECONDS,
    _reset_for_tests,
    consume_confirmation,
    issue_confirmation,
)


@pytest.fixture(autouse=True)
def _clean():
    _reset_for_tests()
    yield
    _reset_for_tests()


def _issue(action="demo", params=None, session="s1", now=1000.0):
    return issue_confirmation(
        session_id=session,
        action=action,
        params=params or {"x": 1},
        summary="预览",
        now=now,
    )


def test_issue_returns_token_and_ttl():
    token, ttl = _issue()
    assert token
    assert ttl == TOKEN_TTL_SECONDS


def test_consume_requires_user_confirmation_word():
    token, _ = _issue(now=1000.0)
    ok, reason = consume_confirmation(
        session_id="s1",
        token=token,
        action="demo",
        params={"x": 1},
        user_message="停止因子研发",
        now=1001.0,
    )
    assert ok is False
    assert "确认" in reason


def test_consume_with_confirmation_word_executes_once():
    token, _ = _issue(now=1000.0)
    ok, reason = consume_confirmation(
        session_id="s1",
        token=token,
        action="demo",
        params={"x": 1},
        user_message="确认执行",
        now=1001.0,
    )
    assert ok is True, reason

    ok2, _ = consume_confirmation(
        session_id="s1",
        token=token,
        action="demo",
        params={"x": 1},
        user_message="确认执行",
        now=1002.0,
    )
    assert ok2 is False


def test_token_optional_matches_session_action_params():
    _issue(now=1000.0)
    ok, reason = consume_confirmation(
        session_id="s1",
        token="",
        action="demo",
        params={"x": 1},
        user_message="确认执行",
        now=1001.0,
    )
    assert ok is True, reason


def test_wrong_session_rejected():
    token, _ = _issue(session="s1", now=1000.0)
    ok, reason = consume_confirmation(
        session_id="s2",
        token=token,
        action="demo",
        params={"x": 1},
        user_message="确认执行",
        now=1001.0,
    )
    assert ok is False
    assert "会话" in reason


def test_wrong_params_rejected():
    token, _ = _issue(params={"x": 1}, now=1000.0)
    ok, reason = consume_confirmation(
        session_id="s1",
        token=token,
        action="demo",
        params={"x": 2},
        user_message="确认执行",
        now=1001.0,
    )
    assert ok is False
    assert "参数" in reason


def test_expired_token_rejected():
    token, _ = _issue(now=1000.0)
    ok, reason = consume_confirmation(
        session_id="s1",
        token=token,
        action="demo",
        params={"x": 1},
        user_message="确认执行",
        now=1000.0 + TOKEN_TTL_SECONDS + 1,
    )
    assert ok is False
    assert "过期" in reason
