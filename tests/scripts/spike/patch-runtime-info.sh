#!/usr/bin/env bash
set -euo pipefail
F=/opt/rdagent/lib/python3.11/site-packages/rdagent/scenarios/shared/get_runtime_info.py
cp "$F" "$F.bak-spike-patch"
/opt/bootstrap/bin/python - "$F" <<'PY'
import io, sys
p = sys.argv[1]
s = io.open(p, encoding="utf-8").read()
old = '''    stdout = implementation.execute(env=env, entry=f"python {fname}")
    # Extract JSON from stdout (skip CUDA/container warnings)
    json_match = re.search(r"\\{.*\\}", stdout, re.DOTALL)
    return json.dumps(json.loads(json_match.group()), indent=2)'''
new = '''    stdout = implementation.execute(env=env, entry=f"python {fname}")
    # Extract JSON from stdout (skip CUDA/container warnings)
    json_match = re.search(r"\\{.*\\}", stdout, re.DOTALL)
    if json_match is None:
        # [spike-local patch 2026-10-16] WSL 上 LocalEnv 执行 context 下该内省脚本
        # 可能无 JSON 输出（非致命元信息）——降级返回占位描述，MUST NOT 阻断主循环。
        # （备份见 .bak-spike-patch；上游修复后可移除）
        import json as _json
        return _json.dumps({"note": "runtime environment info unavailable (WSL local env)"}, indent=2)'''
assert old in s
s = s.replace(old, new)
io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("patched")
PY
/opt/rdagent/bin/python -c "import importlib, rdagent.scenarios.shared.get_runtime_info as m; importlib.reload(m); print('import ok')"
