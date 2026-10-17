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

## 2026-10-16 晚：fin_factor 单循环完整跑通（⑥ 前的核心验证）

- `launch-fin-factor.sh`（DOCKER_HOST=bind socket + conda 激活语义）→ 单循环 5/5 步 100% 完成
- LLM 生成因子代码 → local_qlib 容器内真实数据评估（47,378 非空因子值）→ CoSTEER 判 SUCCESS
- 遇到的三个坑（均已绕过/修复，脚本含修复）：
  1. 镜像构建内 `git clone github.com/microsoft/qlib` 被墙 → Dockerfile 补丁换 ghfast 镜像（.bak-spike-patch）
  2. RD-Agent 默认 embedding=text-embedding-3-small 在 bigmodel 不存在（错误码 1211）→ `.env` 补 `EMBEDDING_MODEL=openai/embedding-3`（实测 dim 2048 ✓）
  3. LiteLLM 对 `openai/` 前缀回落读 OPENAI_API_KEY → `.env` 补 OPENAI_API_KEY/OPENAI_API_BASE
- 遗留：MLflow 文件存储后端弃用告警（record 步）；WSL 下 runtime info 内省补丁（见 patch-runtime-info.sh）

## 2026-10-17：chat 档切 opencode deepseek-v4.1-flash（多循环实证）

- **凭据/端点分离**：chat = opencode `deepseek-v4.1-flash`（`CHAT_OPENAI_API_KEY`/`CHAT_OPENAI_BASE_URL`），
  embedding 留 bigmodel `embedding-3`（`OPENAI_API_KEY`/`OPENAI_API_BASE` 走 env 回落）。
  **opencode 强制 `x-opencode-session` 头（缺头 400 MissingSessionID）**——`patch-opencode-chat.py`
  给 RD-Agent 的 `rdagent/oai/backend/litellm.py` 打补丁：completion 调用消费
  `chat_openai_*` 字段 + opencode 端点注入会话头（备份 `.bak-opencode-patch`）。
- **启动器（`launch-fin-factor.sh`）两条硬语义**（历次踩坑）：
  1. `DOCKER_HOST` = GUI 解锁后的 bind socket；
  2. **必须 `conda.sh + conda activate rdagent4qlib`**——只把 env 的 bin 加 PATH 不够：
     conda 本体解析不到 → LocalEnv 误判「环境缺失」→ `conda create` 退 127 → running 步
     `qrun: No such file or directory` 连败（2026-10-17 首轮 15 次试验全废实测）。
- **实证**：修复后 14 个完整循环 × 5 步（direct_exp_gen→coding→running→feedback→record）
  全部通过，115 个候选实现落工作区；`--loop-n` 默认无界（手动停）。

## 2026-10-17：研发代理与控制面（delta add-factor-research-control）

`factor_research_agent.py` = 观测（采集快照）+ 控制（期望态调和）合一，**常驻、独立于看护存活**
（看护停止后继续写 `running=false` 快照并受理控制——否则停机后快照陈旧、开关无法再开启）。

**部署步骤**（替换旧采集守护；看护不重启）：

```bash
# 1) 推文件（宿主侧，仓库根；stdin 管道入 distro）
wsl -d rdagent -u root -- bash -c 'cat > /root/factor_research_agent.py'    < tests/scripts/spike/factor_research_agent.py
wsl -d rdagent -u root -- bash -c 'cat > /root/collect_factor_status.py'    < tests/scripts/spike/collect_factor_status.py
wsl -d rdagent -u root -- bash -c 'cat > /root/start-factor-research.sh'    < tests/scripts/spike/start-factor-research.sh
wsl -d rdagent -u root -- bash -c 'cat > /root/stop-factor-research.sh'     < tests/scripts/spike/stop-factor-research.sh
wsl -d rdagent -u root -- bash -c 'cat > /root/status-factor-research.sh'   < tests/scripts/spike/status-factor-research.sh
wsl -d rdagent -u root -- chmod +x /root/start-factor-research.sh /root/stop-factor-research.sh /root/status-factor-research.sh

# 2) 停旧采集守护（若在跑），起新代理守护（看护不重启）
wsl -d rdagent -u root -- bash -c '
  W=/root/rdagent-work
  [ -f "$W/factor_status_collector.pid" ] && kill "$(cat "$W/factor_status_collector.pid")" 2>/dev/null; rm -f "$W/factor_status_collector.pid"
  python3 /root/factor_research_agent.py --ensure-daemon
  python3 /root/factor_research_agent.py --reconcile-once
'
# 3) 核对：status 脚本可见「代理: RUNNING」+ 控制文件；快照 collected_at 持续推进
wsl -d rdagent -u root -- bash /root/status-factor-research.sh
```

**控制面语义**（界面开关 / `start` / `stop` 三入口同一条意图源）：

- 控制文件：`/mnt/d/WorkSpace/finance_analysis_agent/data/factor_research/control.json`
  （宿主 `data/` 桥反向；后端 `PUT /api/factor-research/control` 原子写，代理 ≤15s 轮询调和）；
- `desired_running=true` 且未运行 → 拉起；`false` 且运行中 → 停止（终止当前会话）；
  期望=实态 → 零动作；文件缺失/损坏 → **不干预**（fail-safe）；
- 手动 `start/stop` 脚本会同步写期望态（否则代理会把「手动停止」按旧意图扳回去）；
- 代理与看护都是**手动常驻**（distro 重启后需重新 `start`；不做开机自启——与看护同级运维前提）。
