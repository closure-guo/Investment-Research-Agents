# RD-Agent spike 环境脚本（unify-quant-research-architecture P0-3，2026-10-16）

在 WSL2 distro `rdagent`（Ubuntu 24.04，`wsl --import` 自 NJU 镜像 rootfs）内执行。
环境进度与卡点见 `openspec/changes/unify-quant-research-architecture/tasks.md` P0-3。

## 已就绪（2026-10-16 实测）
- distro：rdagent（wsl --import，root 默认）+ Python 3.11.17（uv 独立工具链）
- rdagent（v 最新，pip/TUNA）+ pyqlib 0.9.7 + conda env `rdagent4qlib`（py3.10 + qlib/评估依赖）
- Qlib 数据：`~/.qlib/qlib_data/cn_data`（investment_data 2026-10-15 版，2000–2026-10-15，850MB）
- LLM：bigmodel glm-5.3-flash（`/root/rdagent-work/.env`，chat 实测应答 ✓）

## 执行顺序
1. `setup-rdagent.sh`（stdin 管道入 distro：`wsl -d rdagent -u root -- bash -s < <script>`）
2. `setup-conda.sh`
3. `config-llm.sh`（需 CHAT_KEY 环境变量：`CHAT_KEY=$(grep ^JUDGE_API_KEY= .env | cut -d= -f2) CHAT_KEY="$CHAT_KEY" WSLENV=CHAT_KEY/u wsl ...`）
4. `patch-runtime-info.sh`（WSL 上 runtime info 内省可能无 JSON——降级放行，非致命；备份 .bak-spike-patch）

## 卡点（唯一）
fin_factor 循环 init 需要 Docker SDK（/var/run/docker.sock）——Docker Desktop 29.6.1 的
WSL 集成对 `rdagent` distro 未注入（settings-store.json 键 `EnableIntegrationWithAdditionalWslDistros`
CLI 修改 + 两种重启顺序均未生效；共享 socket 对非集成 distro 不可见）。
**解锁：Docker Desktop GUI → Settings → Resources → WSL Integration → 勾选 rdagent → Apply & Restart。**

## 网络环境备忘（本机实测 2026-10-16）
- raw.githubusercontent.com / objects.githubusercontent.com（release 资产直连）**不可达**
- PyPI 走 TUNA 镜像 ✓；github release 资产走 ghfast.top/gh-proxy.com ✓（8MB/s）
- Ubuntu WSL rootfs：NJU 镜像 `ubuntu-cloud-images/wsl` ✓（1.2MB/s+）
