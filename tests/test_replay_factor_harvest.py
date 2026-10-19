"""重放脚本单测（recover-factor-supply-losses，零网络）。

只测脚本职责（状态/候选解析、重放选择、dry-run、退出码、参数透传）——
重放语义本身由 ``tests/factors/test_harvest.py::TestRetryStatusesReplay`` 覆盖；
此处 monkeypatch ``run_factor_harvest``。
"""

from __future__ import annotations

import json

import scripts.replay_factor_harvest as replay_module
from scripts.replay_factor_harvest import main


def _write(tmp_path, candidates: list[str], processed: dict[str, dict]) -> tuple[str, str]:
    cand_path = tmp_path / "candidates.json"
    cand_path.write_text(
        json.dumps({"candidates": [{"name": name} for name in candidates]}), encoding="utf-8"
    )
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"processed": processed}), encoding="utf-8")
    return str(cand_path), str(state_path)


def test_dry_run_lists_selection_and_skips_execution(tmp_path, monkeypatch, capsys):
    cand, state = _write(
        tmp_path,
        ["a", "b", "c"],
        {
            "a": {"status": "untranslatable"},
            "b": {"status": "rejected"},
            "c": {"status": "transcription_error"},
        },
    )
    called: list[dict] = []
    monkeypatch.setattr(replay_module, "run_factor_harvest", lambda **kw: called.append(kw))

    assert (
        main(["--statuses", "untranslatable", "--candidates", cand, "--state", state, "--dry-run"])
        == 0
    )
    out = capsys.readouterr().out
    assert "a: untranslatable" in out
    assert "b: rejected" not in out  # 未命中状态不入选
    assert called == []  # dry-run 不执行


def test_executes_with_statuses_and_limit_and_prints_migration(tmp_path, monkeypatch, capsys):
    cand, state = _write(
        tmp_path,
        ["a", "b"],
        {"a": {"status": "untranslatable"}, "b": {"status": "untranslatable"}},
    )
    seen: dict = {}

    def fake_harvest(**kwargs):
        seen.update(kwargs)
        # 模拟重放后状态迁移
        payload = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        payload["processed"]["a"] = {"status": "rejected", "gate": 2, "reason": "WEAK_ICIR"}
        (tmp_path / "state.json").write_text(json.dumps(payload), encoding="utf-8")
        return {"proposed": 2, "replayed": 2, "admitted": 0, "rejected": 1, "errors": 1}

    monkeypatch.setattr(replay_module, "run_factor_harvest", fake_harvest)

    assert (
        main(
            [
                "--statuses",
                "untranslatable,transcription_error",
                "--candidates",
                cand,
                "--state",
                state,
                "--limit",
                "2",
                "--db",
                str(tmp_path / "lib.db"),
            ]
        )
        == 0
    )
    assert seen["retry_statuses"] == ("untranslatable", "transcription_error")
    assert seen["limit"] == 2
    assert seen["db_path"] == str(tmp_path / "lib.db")
    out = capsys.readouterr().out
    assert "a: untranslatable -> rejected" in out
    assert '"replayed": 2' in out


def test_missing_candidates_file_exits_2(tmp_path, capsys):
    assert main(["--statuses", "untranslatable", "--candidates", str(tmp_path / "none.json")]) == 2
    assert "候选文件读取失败" in capsys.readouterr().err


def test_nonexistent_state_exits_2(tmp_path, capsys):
    """严格读态（review 修复）：状态缺失 → 退 2，绝不把坏状态读成 {} 后整体改写。"""
    cand, _ = _write(tmp_path, ["a"], {})
    assert (
        main(
            [
                "--statuses",
                "untranslatable",
                "--candidates",
                cand,
                "--state",
                str(tmp_path / "absent.json"),
            ]
        )
        == 2
    )
    assert "状态文件读取失败" in capsys.readouterr().err


def test_unreplayable_status_exits_2(tmp_path, capsys):
    """`admitted` 等不可重放状态拒绝（重放会把库内 active 的审计状态改写成「已拒」）。"""
    cand, state = _write(tmp_path, ["a"], {})
    rc = main(["--statuses", "admitted", "--candidates", cand, "--state", state])
    assert rc == 2
    assert "不可重放状态" in capsys.readouterr().err


def test_empty_statuses_exits_2(tmp_path, capsys):
    cand, state = _write(tmp_path, ["a"], {})
    assert main(["--statuses", " , ", "--candidates", cand, "--state", state]) == 2
    assert "不能为空" in capsys.readouterr().err


def test_limit_out_of_bounds_exits_2(tmp_path, capsys):
    cand, state = _write(tmp_path, ["a"], {})
    for bad in ("0", "101"):
        rc = main(
            ["--statuses", "untranslatable", "--candidates", cand, "--state", state, "--limit", bad]
        )
        assert rc == 2
        assert "1-100" in capsys.readouterr().err
