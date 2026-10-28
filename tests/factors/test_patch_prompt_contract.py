"""patch-prompt-contract.py 单测：桩文件来自真实 distro 快照夹具（非实现自证）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT_SRC = _REPO / "tests" / "scripts" / "spike" / "patch-prompt-contract.py"
_FIXTURE = _REPO / "tests" / "fixtures" / "distro_factor_proposal_20261028.txt"


def _load_script(tmp_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("patch_prompt_contract_under_test", _SCRIPT_SRC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TARGET = tmp_path / "factor_proposal.py"
    return module


def _fixture_text() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def _write_target(tmp_path: Path, text: str, *, sibling: bool = True) -> Path:
    target = tmp_path / "factor_proposal.py"
    target.write_text(text, encoding="utf-8")
    if sibling:
        (tmp_path / "explored_feedback.py").write_text(
            "def render_contract_block(path=None):\n    return ''\n", encoding="utf-8"
        )
    return target


def test_old_block_matches_captured_distro_snapshot(tmp_path: Path) -> None:
    """OLD 锚点漂移守卫：与 2026-10-28 只读捕获的真实 distro 快照逐字一致。"""
    module = _load_script(tmp_path)
    assert module.OLD in _fixture_text()


def test_patch_fresh_inserts_contract(tmp_path: Path) -> None:
    module = _load_script(tmp_path)
    target = _write_target(tmp_path, _fixture_text())
    assert module.main() == 0
    patched = target.read_text(encoding="utf-8")
    assert "import render_contract_block, render_explored_block" in patched
    assert "_spec_text = T(" in patched
    assert patched.count("_contract = render_contract_block()") == 1
    backup = target.with_name(target.name + module.BACKUP_SUFFIX)
    assert backup.exists()


def test_patch_idempotent(tmp_path: Path) -> None:
    module = _load_script(tmp_path)
    target = _write_target(tmp_path, _fixture_text())
    assert module.main() == 0
    first = target.read_text(encoding="utf-8")
    assert module.main() == 0
    assert target.read_text(encoding="utf-8") == first


def test_patch_rejects_marker_in_comment(tmp_path: Path) -> None:
    """MARKER 不得是裸子串：注释里提到函数名不能被当「已打过」。"""
    module = _load_script(tmp_path)
    text = _fixture_text().replace(
        '        hypothesis_and_feedback = "x"\n',
        '        hypothesis_and_feedback = "x"  # TODO: render_contract_block() later\n',
    )
    target = _write_target(tmp_path, text)
    assert module.main() == 0
    patched = target.read_text(encoding="utf-8")
    assert patched.count("_contract = render_contract_block()") == 1


def test_patch_aborts_without_backup_when_anchor_missing(tmp_path: Path) -> None:
    """探针先于备份：锚点缺失 abort 时不得留下备份文件。"""
    module = _load_script(tmp_path)
    target = _write_target(tmp_path, "def unrelated():\n    pass\n")
    assert module.main() == 1
    assert not target.with_name(target.name + module.BACKUP_SUFFIX).exists()
    assert "render_contract_block" not in target.read_text(encoding="utf-8")


def test_patch_aborts_when_sibling_module_missing(tmp_path: Path) -> None:
    """依赖探针：同级 explored_feedback.py 未先部署（缺 render_contract_block）→ 拒绝打补丁。"""
    module = _load_script(tmp_path)
    target = _write_target(tmp_path, _fixture_text(), sibling=False)
    assert module.main() == 1
    assert "_contract = render_contract_block()" not in target.read_text(encoding="utf-8")


def test_patch_rolls_back_on_compile_error(tmp_path: Path) -> None:
    """py_compile 失败必须回滚到原文（不得留下半改写文件）。"""
    module = _load_script(tmp_path)
    original = _fixture_text() + "def broken(:\n    pass\n"
    target = _write_target(tmp_path, original)
    assert module.main() == 1
    assert target.read_text(encoding="utf-8") == original


def test_patch_inserts_import_when_fresh_distro(tmp_path: Path) -> None:
    """未打 patch-1 的干净 distro（无 explored import 行）：锚点后插入完整合并 import。"""
    module = _load_script(tmp_path)
    text = _fixture_text().replace(
        "from rdagent.scenarios.qlib.proposal.explored_feedback import render_explored_block\n", ""
    )
    target = _write_target(tmp_path, text)
    assert module.main() == 0
    patched = target.read_text(encoding="utf-8")
    assert "import render_contract_block, render_explored_block" in patched
