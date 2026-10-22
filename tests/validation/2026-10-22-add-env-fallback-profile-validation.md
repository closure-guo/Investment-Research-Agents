# 验证报告：add-env-fallback-profile

**日期**: 2026-10-22
**验证人**: agent（机器项 + 容器内真实链路）
**关联 delta**: `openspec/changes/add-env-fallback-profile/`
**E2E 门禁**: 不适用（LLM 配置解析，无 UI/SSE 变更）

## 背景

2026-10-20 晚主 provider（opencode zen）连续约 1 小时 5xx/连接错误，4 个会话在同一端点重试 3 次后全部 failed；近 7 天 24 次失败中 9 次属该桶。registry 的 fallback 链只服务命名 preset，env 配置（生产路径）没有链成员。

## 变更

- `registry.register_preset()`：运行时注册 preset（env 注入成员用）。
- `resolver._env_fallback_profile()`：`LLM_FALLBACK_MODEL/BASE_URL/API_KEY` → 动态 profile
  `env-fallback:<model>`（能力档按模型名推断；半套配置不注册）。
- `resolver._with_env_fallback()`：挂到主 profile 的 `fallback`；`purpose=judge` 不挂。
- `.env` 配 Kimi `k3-256k`（`https://api.kimi.com/coding/v1`）为备用端点；`.env.example` 增注释示例。

## 机器验证

| 项 | 命令 | 结果 |
|---|---|---|
| 新用例 | `uv run pytest tests/llm/test_env_fallback_profile.py -q` | 5 passed（先红：2 failed） |
| LLM 全量 | `uv run pytest tests/llm -q` | 375 passed |
| Lint | `uv run ruff check`（resolver/registry/新测试） | All checks passed |
| 类型 | `uv run mypy src/finance_agent/llm/resolver.py src/finance_agent/llm/registry.py` | 1 个既有错误（`_parse_context_length` 返回 Any，非本次引入、非本次触碰行） |
| OpenSpec | `openspec validate add-env-fallback-profile --strict` | valid |

## 真实链路验证（容器内、生产 env）

链装配：

```
$ docker compose exec -T backend python -c "from finance_agent.llm.gateway import fallback_attempt_plan; ..."
['env:openai/deepseek-v4.1-flash', 'env-fallback:openai/k3-256k']
[None, 'env-fallback:openai/k3-256k']
```

成员端点小请求（经网关 `complete_text`，preset=成员名）：

```
content= 'OK'
raw_content= 'OK'
raw_reasoning= 'The user is asking me to only reply with "OK" ...'
finish= stop
```

## 边界与遗留

1. **备用端点是 Kimi coding 端点**，key 来自 `.env.bak-kimi-20261013`（实测 200）；若该订阅到期需更换——`.env.example` 已留通用占位。
2. **Kimi reasoning 与正文共享 max_tokens**：`max_tokens=16` 时正文为空（推理吃满），正式节点预算充足不受影响；若后续出现 fallback 成员空正文，优先查小预算调用点。
3. **未做端到端断供演练**：验证了链装配与成员可用，未人为制造 primary 5xx 观察整链切换（可用 `LLM_BASE_URL` 指向不可达端点复现，属破坏性演练，留给下轮）。
4. 本次未触发 hosted 评估（评估被 prompt 版本门禁阻断，见独立报告）。
