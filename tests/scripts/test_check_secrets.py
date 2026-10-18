"""``check_secrets.py`` 密钥扫描器单测（防泄露 CI 门禁的守门测试）。

钉三件事：
1. **新 opencode key 格式（``oc_sk_`` 前缀）必须被覆盖**——2026-10-18 轮换后网关
   改用该格式，而 GitHub push protection 不认 opencode 提供商（实测免费计划下
   非提供方模式/自定义模式不可用），本扫描器是唯一兜底 → 盲点即泄露通道；
2. 既有格式（sk- / pk-lf- / sk-lf- / tvly- / AKIA）保持覆盖；
3. 普通文本不误报（门禁可持续的前提）。

样本一律运行时拼接（字面量会命中扫描器自身 → 门禁自伤）。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import check_secrets


def _matches_any(sample: str) -> bool:
    return any(p.search(sample) for p in check_secrets.KEY_PATTERNS)


def test_new_opencode_key_format_is_covered():
    """新格式 oc_sk_（轮换后唯一在用的网关 key 形态）必须在扫描面内。"""
    sample = "oc_sk_91765cadc2d3_" + "A" * 24
    assert _matches_any(sample), "oc_sk_ 格式未被任何模式覆盖——新 key 泄露将无人拦截"


def test_existing_provider_formats_still_covered():
    samples = {
        "openai-style": "sk-" + "a" * 32,
        "langfuse-public": "pk-lf-" + "b" * 16,
        "langfuse-secret": "sk-lf-" + "c" * 16,
        "tavily": "tvly-dev-" + "d" * 16,
        "aws-akia": "AKIA" + "ABCDEFGHIJKLMNOP",
        "zhipu-bigmodel": "a" * 32 + "." + "B" * 16,
    }
    for name, sample in samples.items():
        assert _matches_any(sample), f"{name} 模式回归丢失"


def test_plain_text_not_flagged():
    """普通配置/文档文本不得误报（占位符、变量名、短 token）。"""
    for text in (
        "LLM_API_KEY=your-key-here",
        "# LANGFUSE_SECRET_KEY=sk-lf-xxx",
        "OPENCODE_API_KEY_DEEPSEEK_V4_FLASH",
        "key = os.environ['LLM_API_KEY']",
    ):
        assert not _matches_any(text), f"误报：{text!r}"


def test_repo_scan_passes():
    """当前仓库树整体扫描必须通过（扫描器自身的端到端回归）。"""
    assert check_secrets.main() == 0


# ── 历史扫描（--history）：当前树只答"现在有没有"，历史扫描答"曾经有没有进过仓" ──


def _run_git(cmd: list[str], cwd: Path) -> None:
    """跑固定 git 子命令（测试基建；noqa 只挂这一处，避免被 ruff-format 换行拆散）。"""
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True)  # noqa: S603


def _make_repo(tmp_path: Path) -> Path:
    """临时 git 仓库（历史扫描测试用）。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
    ):
        _run_git(cmd, repo)
    return repo


def _commit(repo: Path, filename: str, content: str, message: str) -> None:
    (repo / filename).write_text(content, encoding="utf-8")
    _run_git(["git", "add", "."], repo)
    _run_git(["git", "commit", "-qm", message], repo)


def test_history_scan_flags_secret_committed_then_removed(tmp_path, monkeypatch):
    """密钥提交后又删除：当前树干净，历史扫描必须抓到（本仓库两次泄露的真实形态）。"""
    repo = _make_repo(tmp_path)
    _commit(repo, "a.py", "KEY = " + repr("oc_sk_" + "B" * 30) + "\n", "add secret")
    _commit(repo, "a.py", "KEY = os.environ['LLM_API_KEY']\n", "remove secret")
    monkeypatch.setattr(check_secrets, "REPO_ROOT", repo)
    new_hits, known_hits = check_secrets.scan_history()
    assert new_hits, "历史扫描漏报：密钥曾进仓（后又删除）必须被抓到"
    assert known_hits == []
    assert any("oc_sk_" in h for h in new_hits)
    # 当前树扫描应干净（对照：这正是历史扫描存在的理由）
    assert check_secrets.main([]) == 0


def test_history_scan_clean_repo_no_hits(tmp_path, monkeypatch):
    """无密钥历史 → 零命中（门禁不得误报）。"""
    repo = _make_repo(tmp_path)
    _commit(repo, "a.py", "KEY = os.environ['LLM_API_KEY']\n", "clean")
    monkeypatch.setattr(check_secrets, "REPO_ROOT", repo)
    assert check_secrets.scan_history() == ([], [])


def test_history_scan_known_leaked_key_reported_but_not_failing(tmp_path, monkeypatch, capsys):
    """黑名单内的已知泄露 key：报告但不判失败（已吊销+留档，不能把门禁钉死）。"""
    repo = _make_repo(tmp_path)
    _commit(repo, "a.py", "KEY = " + repr(check_secrets.KNOWN_LEAKED_KEYS[0]) + "\n", "known leak")
    monkeypatch.setattr(check_secrets, "REPO_ROOT", repo)
    assert check_secrets.main(["--history"]) == 0
    out = capsys.readouterr().out
    assert "已知泄露" in out
