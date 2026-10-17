#!/usr/bin/env bash
# fin_factor 单循环启动器（unify-quant-research-architecture spike）。
#
# 三件环境语义（历次踩坑固化）：
#   1. DOCKER_HOST 指向 Docker Desktop GUI 解锁后的 bind socket（非集成 distro 不可见
#      /var/run/docker.sock）；
#   2. **conda 必须经 conda.sh + `conda activate rdagent4qlib` 激活**——get_factor_env 读
#      CONDA_DEFAULT_ENV/CONDA_PREFIX，且 running 步的 LocalEnv 需 PATH 里有 conda 本体
#      （只加 env 的 bin 不够：conda 解析不到会误判环境缺失 → `conda create` 127 → qrun
#      找不到；2026-10-17 实测踩坑）；
#   3. /root/rdagent-work/.env 提供 chat/embedding 凭据（2026-10-17 起 chat=opencode
#      deepseek-v4.1-flash + 会话头补丁；embedding=bigmodel embedding-3）。
#
# 用法（在 distro 内或经 wsl 调用）：bash launch-fin-factor.sh [> log 2>&1]
set -euo pipefail

export DOCKER_HOST="unix:///mnt/wsl/docker-desktop-bind-mounts/rdagent/docker.sock"

# conda 激活（PATH/CONDA_* 一次到位）
. /opt/miniconda3/etc/profile.d/conda.sh
conda activate rdagent4qlib

set -a
. /root/rdagent-work/.env
set +a

cd /root/rdagent-work
exec /opt/rdagent/bin/rdagent fin_factor
