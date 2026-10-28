"""patch-prompt-contract.py 单测：tmp 桩文件模拟 distro factor_proposal.py（含 patch-1 后现状）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_SCRIPT_SRC = Path(__file__).resolve().parent / "patch-prompt-contract.py"


def _load_script(tmp_path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("patch_prompt_contract_under_test", _SCRIPT_SRC)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TARGET = tmp_path / "factor_proposal.py"
    return module


def _stub_text(module: ModuleType, *, drop_explored_import: bool = False) -> str:
    """patch-1 已应用形态的 distro 桩：explored import 行 + 精确 OLD 块。"""
    import_line = (
        "from rdagent.scenarios.qlib.proposal.explored_feedback import render_explored_block\n"
    )
    text = (
        "from rdagent.utils.agent.tpl import T\n"
        + ("" if drop_explored_import else import_line)
        + "\n\n"
        "class Gen:\n"
        "    def prepare_context(self, trace):\n"
        "        hypothesis_and_feedback = 'x'\n" + module.OLD + "\n"
    )
    return text


def test_patch_fresh_inserts_contract(tmp_path: Path) -> None:
    module = _load_script(tmp_path)
    target = tmp_path / "factor_proposal.py"
    target.write_text(_stub_text(module), encoding="utf-8")

    assert module.main() == 0
    patched = target.read_text(encoding="utf-8")
    assert "import render_contract_block, render_explored_block" in patched  # import 合并
    assert "_spec_text = T(" in patched
    assert "render_contract_block()" in patched
    assert patched.count("_spec_text = T(") == 1  # 单处注入
    backup = target.with_name(target.name + module.BACKUP_SUFFIX)
    assert backup.exists()


def test_patch_idempotent(tmp_path: Path) -> None:
    module = _load_script(tmp_path)
    target = tmp_path / "factor_proposal.py"
    target.write_text(_stub_text(module), encoding="utf-8")

    assert module.main() == 0
    first = target.read_text(encoding="utf-8")
    assert module.main() == 0  # 重跑不双份
    assert target.read_text(encoding="utf-8") == first


def test_patch_aborts_when_anchor_missing(tmp_path: Path) -> None:
    module = _load_script(tmp_path)
    target = tmp_path / "factor_proposal.py"
    target.write_text("def unrelated():\n    pass\n", encoding="utf-8")

    assert module.main() == 1  # 落点探针：上游变了就 abort，不盲改
    assert "render_contract_block" not in target.read_text(encoding="utf-8")


def test_patch_inserts_import_when_fresh_distro(tmp_path: Path) -> None:
    """未打 patch-1 的干净 distro（无 explored import 行）：锚点后插入完整合并 import。"""
    module = _load_script(tmp_path)
    target = tmp_path / "factor_proposal.py"
    target.write_text(_stub_text(module, drop_explored_import=True), encoding="utf-8")

    assert module.main() == 0
    patched = target.read_text(encoding="utf-8")
    assert "import render_contract_block, render_explored_block" in patched
