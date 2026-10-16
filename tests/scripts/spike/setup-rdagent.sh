#!/usr/bin/env bash
# RD-Agent spike 环境安装（rdagent distro 内执行；unify-quant-research-architecture tasks ①）
set -euo pipefail
export PATH="/opt/bootstrap/bin:/root/.local/bin:$PATH"
export UV_DEFAULT_INDEX="https://pypi.tuna.tsinghua.edu.cn/simple"

echo "== 1. uv venv (python 3.11) =="
uv venv --python 3.11 --seed --clear /opt/rdagent

echo "== 2. pip install rdagent（TUNA 镜像） =="
uv pip install --python /opt/rdagent/bin/python rdagent

echo "== 3. 版本核验 =="
/opt/rdagent/bin/python --version
/opt/rdagent/bin/rdagent --version 2>&1 | head -2 || /opt/rdagent/bin/python -c "import rdagent; print('rdagent import ok')"
echo "SETUP DONE"
