# 密钥卫生手册（轮换 / 防泄露 / 泄露处置）

> 面向：改配置的人与 agent。两条铁律：**密钥永不进仓**；**已泄露的先轮换**，再谈清理历史。
> 配套工具：`tests/scripts/check_secrets.py`（当前树扫描 + `--history` 全历史扫描）、
> `.pre-commit-config.yaml` 的 `check-secrets` 钩子、CI 当前树门禁 + nightly 全历史扫描。

## 1. 密钥清单（在哪、换哪里）

| 密钥 | 存放位置 | 说明 |
|---|---|---|
| `LLM_API_KEY` / `JUDGE_API_KEY` | 本地 `.env`（gitignored） | opencode zen/go 网关；2026-10-18 起为 `oc_sk_` 新格式 |
| `CHAT_OPENAI_API_KEY` / `CHAT_API_KEY` | rdagent 发行版 `/root/rdagent-work/.env` | 研究侧；启动器每会话 `source`，改完下一轮会话自动生效 |
| `OPENCODE_API_KEY_DEEPSEEK_V4_FLASH` | 系统环境变量（HKLM） | 遗留兜底变量 |
| `DEEPSEEK_API_KEY`（GitHub 仓库 Secret） | CI Secrets | nightly @live 用（值=opencode key；命名债待清） |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` | `.env` + 容器 env | 链路追踪 |
| `TAVILY_API_KEY` | `.env` + 系统 env | 搜索工具 |

## 2. 轮换 runbook（四步）

1. **新 key 先探活**：opencode 用 `GET /v1/models`（需 `x-opencode-session` 头）；
   旧 key 过期时网关返回 `api_key_expired`。
2. **替换四处**：本地 `.env`（LLM/JUDGE）→ 发行版 `.env`（CHAT_*）→ 系统 env →
   重建后端容器（`docker compose up -d backend`；先查 `GET /api/sessions` 无 running）。
3. **验证**：容器内 `complete_text` 实探（react + factor_transcription 双 purpose）；
   研究侧看下一轮会话日志 `auth_errors=0`。
4. **记录**：`.env` 注释补轮换行 + 记忆留档（格式见 2026-10-18 轮换记录）。

## 3. 防泄露四层防线（现状）

| 层 | 机制 | 位置 |
|---|---|---|
| ① 本地·提交前 | `check-secrets` pre-commit 钩子（全树扫描，命中即拦） | `.pre-commit-config.yaml` |
| ② 平台 | GitHub secret scanning + push protection（免费档；自定义模式不可用） | 仓库 Settings → Code security |
| ③ CI·当前树 | `check_secrets.py`（每次 PR/push，两处） | `.github/workflows/ci.yml` / `e2e-playwright.yml` |
| ④ CI·全历史 | `check_secrets.py --history`（nightly 北京 03:30 + 手动 dispatch） | `ci.yml` 的 `history-secret-scan` job |

判定口径：已知吊销项（`KNOWN_LEAKED_KEYS`）与占位符报告但不判失败；其余命中即红。

## 4. 新 provider / 新 key 形态接入（三步）

1. `KEY_PATTERNS` 加正则（保守：先窄后宽，避免误报）；
2. `tests/scripts/test_check_secrets.py` 格式表加样本（**运行时拼接**，字面量会命中扫描器自身）；
3. `uv run python tests/scripts/check_secrets.py` + `--history` 双模式验证。

## 5. 泄露处置顺序（GitHub 官方 doctrine）

1. **先吊销/轮换**（可能就够了，不必重写历史）；
2. 重写用 `git-filter-repo --sensitive-data-removal` + force-push；
3. 重写不充分（克隆/fork/平台缓存/PR 引用仍可达）→ 联系 GitHub Support 清缓存；
4. 已知泄露 key 记入 `KNOWN_LEAKED_KEYS` 黑名单（防回滚复发）。

## 6. 附：判断某个 key 是否还活着

手动拿疑似 key 打提供商探针（opencode / deepseek 均有 `GET /v1/models`）。
trufflehog 只认其内置 800+ 提供商（opencode 不在内），本项目自有 key 形态需手动探。
