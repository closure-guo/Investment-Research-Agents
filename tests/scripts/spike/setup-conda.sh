#!/usr/bin/env bash
set -euo pipefail
echo "== 1. miniconda（TUNA 镜像） =="
if [ ! -x /opt/miniconda3/bin/conda ]; then
  wget -q https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
  bash /tmp/miniconda.sh -b -p /opt/miniconda3 > /dev/null
fi
CONDA=/opt/miniconda3/bin/conda
$CONDA --version

echo "== 2. conda env rdagent4qlib（python 3.10 + pyqlib 等评估依赖；镜像源，绕 github） =="
if ! $CONDA env list | grep -q rdagent4qlib; then
  $CONDA create -y -q -n rdagent4qlib python=3.10 > /dev/null
fi
/opt/miniconda3/envs/rdagent4qlib/bin/pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple --upgrade pip cython
/opt/miniconda3/envs/rdagent4qlib/bin/pip install -q -i https://pypi.tuna.tsinghua.edu.cn/simple pyqlib catboost xgboost tables lightgbm
/opt/miniconda3/envs/rdagent4qlib/bin/python -c "import qlib; print('conda env qlib:', qlib.__version__)"
echo "CONDA ENV READY"
