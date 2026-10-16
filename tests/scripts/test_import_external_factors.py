"""外部候选导入脚本单测（unify-quant-research-architecture P0-3，零网络）。

只测脚本职责（文件解析 / 参数 / 退出码 / 摘要透传）——五道闸与通道语义由
``tests/factors/test_external_import.py`` 覆盖；此处 monkeypatch 通道函数。
"""

from __future__ import annotations

import json

import scripts.import_external_factors as import_module
from scripts.import_external_factors import main


def test_valid_file_passes_candidates_and_prints_summary(tmp_path, monkeypatch, capsys):
    path = tmp_path / "candidates.json"
    payload = [{"expression": "delta(close,20)", "hypothesis": "测试假设文本（长度达标）"}]
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    seen: dict = {}

    def fake_import(candidates, *, db_path=None, snapshot_db_path=None):
        seen["candidates"] = list(candidates)
        seen["db"] = db_path
        return {"admitted": 1, "rejected": 0, "errors": 0, "rejections": []}

    monkeypatch.setattr(import_module, "run_factor_external_import", fake_import)

    assert main(["--file", str(path), "--db", str(tmp_path / "lib.db")]) == 0

    assert seen["candidates"] == payload
    assert seen["db"] == str(tmp_path / "lib.db")
    out = capsys.readouterr().out
    assert '"admitted": 1' in out


def test_missing_file_exits_2(tmp_path, capsys):
    assert main(["--file", str(tmp_path / "nope.json")]) == 2
    assert "候选文件读取失败" in capsys.readouterr().err


def test_non_list_payload_exits_2(tmp_path, capsys):
    path = tmp_path / "bad.json"
    path.write_text('{"expression": "x"}', encoding="utf-8")
    assert main(["--file", str(path)]) == 2
    assert "JSON 数组" in capsys.readouterr().err
