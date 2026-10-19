/**
 * E2E 后端统一环境隔离（issue #328）。
 *
 * 本机 `.env` 常配置 `LANGFUSE_*`（默认指向 localhost:3000），但 E2E stub 栈
 * 并不启动 Langfuse。后端在 Langfuse 不可达时仍会尝试拉 prompt / 导出 span：
 * 实测 5 层管线在 L4 风控后停住，日志出现
 * `Failed to export span batch due to timeout`，管线报告 90s 内不产出。
 *
 * 在 Playwright 配置加载期清空凭据，webServer 子进程继承空值；后端
 * `load_dotenv()` 默认不覆盖已存在键（含空串），`get_langfuse()` 因凭据为空
 * 返回 None，prompt loader 回退本地权威源，E2E 不再依赖 Langfuse 在线。
 */
process.env.LANGFUSE_PUBLIC_KEY = ''
process.env.LANGFUSE_SECRET_KEY = ''
process.env.LANGFUSE_HOST = ''
