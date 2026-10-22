"""把「跨会话因子记忆」注入 RD-Agent 生成侧（幂等补丁）。

做法：在 `QlibFactorHypothesisGen.prepare_context` 里，给 `hypothesis_and_feedback`
末尾追加 `render_explored_block()` 的渲染结果（首轮与后续轮都追加；首轮正是跨会话的盲区）。

用法（在 WSL distro `rdagent` 内以 root 执行）::

    wsl -d rdagent -u root -- bash -c "tr -d '\r' > /root/explored_feedback.py" < tests/scripts/spike/explored_feedback.py
    wsl -d rdagent -u root -- bash -c "cp /opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/proposal/explored_feedback.py{,.bak-20261022} 2>/dev/null || true"
    wsl -d rdagent -u root -- bash -c "tr -d '\r' > /opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/proposal/explored_feedback.py" < tests/scripts/spike/explored_feedback.py
    wsl -d rdagent -u root -- python3 /root/patch-explored-feedback.py

回滚：``cp factor_proposal.py.bak-20261022-explored <原路径>``。
补丁只改 ``factor_proposal.py`` 一处（导入 + 追加块），不触碰 factor_runner / 评分逻辑。
"""

from __future__ import annotations

import py_compile
import shutil
import sys
from pathlib import Path

TARGET = Path(
    "/opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/qlib/proposal/factor_proposal.py"
)
BACKUP_SUFFIX = ".bak-20261022-explored"
IMPORT_LINE = "from rdagent.scenarios.qlib.proposal.explored_feedback import render_explored_block\n"
MARKER = "render_explored_block()"

OLD = '''        hypothesis_and_feedback = (
            T("scenarios.qlib.prompts:hypothesis_and_feedback").r(
                trace=trace,
            )
            if len(trace.hist) > 0
            else "No previous hypothesis and feedback available since it's the first round."
        )'''

NEW = OLD + '''
        if len(trace.hist) == 0:  # 跨会话记忆只在首轮注入（会话内反馈由 trace 自身承担）
            _explored_block = render_explored_block()
            if _explored_block:
                hypothesis_and_feedback = f"{hypothesis_and_feedback}\\n\\n{_explored_block}"'''


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

    if IMPORT_LINE not in source:
        anchor = "from rdagent.utils.agent.tpl import T\n"
        if anchor not in source:
            print("anchor import not found; aborting", file=sys.stderr)
            return 1
        source = source.replace(anchor, anchor + IMPORT_LINE, 1)

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
