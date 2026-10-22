"""run-info 版本可观测性：构建期 GIT_COMMIT 优先于 git 探测（2026-10-22）。"""

from __future__ import annotations

from finance_agent.api import _git_commit


class TestGitCommit:
    def test_build_time_env_wins_and_is_shortened(self, monkeypatch):
        monkeypatch.setenv("GIT_COMMIT", "0123456789abcdef0123456789abcdef01234567")
        assert _git_commit() == "0123456789ab"

    def test_finance_agent_env_alias(self, monkeypatch):
        monkeypatch.setenv("GIT_COMMIT", "")
        monkeypatch.setenv("FINANCE_AGENT_GIT_COMMIT", "abcdef1234567890")
        assert _git_commit() == "abcdef123456"

    def test_blank_env_falls_back_to_repo_detection(self, monkeypatch):
        monkeypatch.setenv("GIT_COMMIT", "   ")
        monkeypatch.delenv("FINANCE_AGENT_GIT_COMMIT", raising=False)
        commit = _git_commit()
        # 仓库内运行时应能探测到 12 位哈希；非 git 环境为 None（两种情况都不允许空串）
        assert commit is None or (
            len(commit) == 12 and all(c in "0123456789abcdef" for c in commit)
        )
