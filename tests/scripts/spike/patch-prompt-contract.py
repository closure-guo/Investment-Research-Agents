"""把「DSL 表达契约」注入 RD-Agent 生成 prompt 的任务指令层（幂等补丁）。

与 `patch-explored-feedback.py`（记忆块→上下文段）互补：本补丁把
`render_contract_block()` 渲染的契约段前置到 `hypothesis_specification`
（`scenarios.qlib.prompts:factor_hypothesis_specification` 的渲染产物，属
prompt 模板本体的任务指令层、每轮生效），让约束从「上下文」升格为「任务定义」。

用法（在 WSL distro `rdagent` 内以 root 执行；部署门：10-31 #482 判效读数归档后）::

    wsl -d rdagent -u root -- bash -c "tr -d '\r' > /opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/proposal/explored_feedback.py" < tests/scripts/spike/explored_feedback.py
    wsl -d rdagent -u root -- bash -c "tr -d '\r' > /root/patch-prompt-contract.py" < tests/scripts/spike/patch-prompt-contract.py
    wsl -d rdagent -u root -- python3 /root/patch-prompt-contract.py

回滚：``cp factor_proposal.py.bak-20261028-contract <原路径>``。
落点探针：OLD 块逐字匹配 distro 现状（patch-1 已应用形态）；上游变更即 abort，
不盲改。契约源缺失时渲染函数返回空串，注入自然跳过（fail-safe）。
"""

from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path

TARGET = Path(
    "/opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/proposal/factor_proposal.py"
)
BACKUP_SUFFIX = ".bak-20261028-contract"
IMPORT_OLD = "from rdagent.scenarios.qlib.proposal.explored_feedback import render_explored_block\n"
IMPORT_COMBINED = (
    "from rdagent.scenarios.qlib.proposal.explored_feedback import "
    "render_contract_block, render_explored_block\n"
)
ANCHOR = "from rdagent.utils.agent.tpl import T\n"
MARKER = "render_contract_block()"

OLD = """        context_dict = {
            "hypothesis_and_feedback": hypothesis_and_feedback,
            "last_hypothesis_and_feedback": last_hypothesis_and_feedback,
            "RAG": (
                "Try the easiest and fastest factors to experiment with from various perspectives first."
                if len(trace.hist) < 15
                else "Now, you need to try factors that can achieve high IC (e.g., machine learning-based factors)."
            ),
            "hypothesis_output_format": T("scenarios.qlib.prompts:factor_hypothesis_output_format").r(),
            "hypothesis_specification": T("scenarios.qlib.prompts:factor_hypothesis_specification").r(),
        }
        return context_dict, True"""

NEW = """        _spec_text = T("scenarios.qlib.prompts:factor_hypothesis_specification").r()
        _contract = render_contract_block()  # 任务指令层 DSL 契约（fail-safe：缺失→空串跳过注入）
        if _contract:
            _spec_text = f"{_contract}\\n\\n{_spec_text}"

        context_dict = {
            "hypothesis_and_feedback": hypothesis_and_feedback,
            "last_hypothesis_and_feedback": last_hypothesis_and_feedback,
            "RAG": (
                "Try the easiest and fastest factors to experiment with from various perspectives first."
                if len(trace.hist) < 15
                else "Now, you need to try factors that can achieve high IC (e.g., machine learning-based factors)."
            ),
            "hypothesis_output_format": T("scenarios.qlib.prompts:factor_hypothesis_output_format").r(),
            "hypothesis_specification": _spec_text,
        }
        return context_dict, True"""


def main() -> int:
    if not TARGET.is_file():
        print(f"target not found: {TARGET}", file=sys.stderr)
        return 1
    source = TARGET.read_text(encoding="utf-8")
    if MARKER in source:
        print("already patched; nothing to do")
        return 0

    backup = TARGET.with_name(TARGET.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(TARGET, backup)
        print(f"backup: {backup}")

    if IMPORT_OLD in source:
        source = source.replace(IMPORT_OLD, IMPORT_COMBINED, 1)
    elif "render_contract_block" not in source:
        if ANCHOR not in source:
            print("anchor import not found; aborting", file=sys.stderr)
            return 1
        source = source.replace(ANCHOR, ANCHOR + IMPORT_COMBINED, 1)

    if OLD not in source:
        print("target block not found (upstream changed?); aborting", file=sys.stderr)
        return 1
    source = source.replace(OLD, NEW, 1)

    TARGET.write_text(source, encoding="utf-8")
    py_compile.compile(str(TARGET), doraise=True)
    print(f"patched + compiled: {TARGET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
