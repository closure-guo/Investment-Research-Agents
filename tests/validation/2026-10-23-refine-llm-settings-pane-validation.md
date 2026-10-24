# 人工验证报告: refine-llm-settings-pane

**日期**: 2026-10-23（自动验证）；2026-10-24（浏览器人工抽查，真实 provider）
**验证人**: AI（自动化验证 + 代码审查 + 浏览器人工抽查）
**关联 delta**: `openspec/changes/refine-llm-settings-pane/`
**E2E 门禁**: 已执行专项套件 → **2 passed**（`playwright.composer.config.ts`，隔离端口/库）

## 验证结果

| Scenario | 自动覆盖 | 预期行为 | 实际结果 | 通过 |
|---|---|---|---|---|
| 三段分组 | `frontend/src/test/settings/llmConfigPaneRefine.test.tsx` | 连接/模型/高级标题，高级为折叠 `<details>` | 通过 | ✅ |
| Provider 预设卡片 | 同上 | 2 列卡片、选中 `aria-pressed`、点击填充模型与端点 | 通过 | ✅ |
| 已保存密钥不回显 | 同上 + `settingsProfileSwitch.test.tsx` | 输入框 value 恒为空、placeholder 含「已保存」、眼睛切换 password/text | 通过 | ✅ |
| 未改密钥保存保留原值 | 同上 | `onSave` 收到原 apiKey | 通过 | ✅ |
| 字段级校验 | 同上 | contextLength=0/abc、baseUrl=ftp://x → 红字且不调 `onSave` | 通过 | ✅ |
| 最大输出只读 | 同上 + `test_api_llm_config.py` | probe 返回 max_output 时显示数值 | 通过 | ✅ |
| 切换 profile 整体同步 | `settingsProfileSwitch.test.tsx` | 模型名切换、密钥仍不回显 | 通过 | ✅ |

## 验证命令与证据

```
cd tests/e2e/playwright
npx playwright test --config=playwright.composer.config.ts
→ 3 passed（其中 llm-settings-pane.spec.ts 2 条：三段分组/只写密钥/眼睛切换、字段级校验阻止保存并保留原密钥）

cd frontend && npx vitest run
→ Test Files 105 passed, Tests 941 passed

cd frontend && npm run build
→ ✓ 4635 modules transformed, built in 7.73s

uv run pytest tests/test_api_llm_config.py -q → 17 passed
uv run ruff check src/finance_agent/api.py src/finance_agent/llm/resolver.py → All checks passed
```

## 浏览器人工抽查（2026-10-24，真实 provider）

**环境**：同 composer 抽查（uvicorn 8010 隔离库无 `TESTING=1` + vite dev 5182）；profile 由抽查脚本运行时从 `.env` 读取真实凭证注入 localStorage（密钥不落盘、不进截图文案）。

| 检查项 | 预期 | 实际 | 通过 |
|---|---|---|---|
| 三段分组 | 连接/模型/高级 | 三段标题齐备，高级为折叠段 | ✅ |
| Provider 预设卡片 | 2 列卡片、选中态 | DeepSeek/OpenAI/Anthropic/本地 Ollama/自定义 五卡，自定义选中描边 | ✅ |
| 只写密钥 | 输入框恒空 + 「已保存（不回显）」 | placeholder「已保存（不回显）— 输入新值以替换」，值恒空 | ✅ |
| 行内状态点 | 已配置=绿 | API Key 左侧绿点 | ✅ |
| 真实 probe 联动 | 测试连接 → 能力矩阵实测 + max_output 回填 | 「连接成功 · 10971ms · opencode/deepseek-v4.1-flash」；能力矩阵 5/5 过（非流式/工具调用/JSON 输出/流式/工具跟随）+ latency=slow 黄色提示；「最大输出」由「测试连接后探测」变为「8192 tokens」（只读灰底） | ✅ |

证据截图：`tmp/spot-check-20261024/settings-1-layout.png`（probe 前：三段布局 + 未探测提示）、`settings-2-real-probe.png`（probe 后：状态行 + 实测矩阵 + max_output 回填），未入库。

## 异常记录

- 上下文长度输入由 `type="number"` 改为 `type="text" inputMode="numeric"`：`type=number` 下浏览器/jsdom 会吞掉 `abc`，无法呈现「非法值就地报错」的 spec 行为。
- 本次真实 probe 顺带实证 2026-10-23 的 probe 勘误：同一 opencode 端点 `json_output` 实测通过（勘误前记录为不支持），`docs/evals/metrics.md` 10-13 条目勘误随本 change 入库。

## 结论

- [x] 自动化验证全部通过
- [x] 浏览器人工抽查（2026-10-24，真实 probe）通过：三段布局、只写密钥、状态点、max_output 联动
- [x] 可 archive
