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

# 2026-10-22 incident 049：并行 IC 计算的中间文件不得落 /dev/shm。
# /dev/shm 是 tmpfs，上限 = VM 内存 50%（本机 18GB → 8.8GB），写爆即
# `OSError: [Errno 28] No space left on device` 崩会话（05:08 实例）。
# MEMORY_FS_ROOT 必须在本进程 env 里、且在 pandarallel 被 import 前生效
# （pandarallel/core.py:33 在 import 期读它），故放在 exec 之前。
export MEMORY_FS_ROOT=/root/rdagent-work/.pandarallel_mem
mkdir -p "$MEMORY_FS_ROOT"
# pandarallel worker 数：默认按物理核数自动取，但本 VM 内存固定 18GB 且与生产栈共享，
# 核数不是合适的默认。需配合 patch-ic-closure.py（去掉 lambda 对 DataFrame 的闭包捕获）。
export RDAGENT_PANDARALLEL_WORKERS="${RDAGENT_PANDARALLEL_WORKERS:-8}"

cd /root/rdagent-work
exec /opt/rdagent/bin/rdagent fin_factor
