// LLM 配置的本地持久化与请求载荷构建工具。
// 配置以 JSON 形式存入 localStorage（key: fa_llm_config），并兼容旧版 fa_api_key。
// camelCase 为前端内部结构；发送给后端时转换为 snake_case（对齐 Pydantic 模型）。

// localStorage 存储的配置结构（camelCase，前端内部使用）
export interface LLMConfig {
  apiKey: string
  model: string
  baseUrl: string
  thinking: string // "enabled" | "disabled" | ""（空表示未设置，回退后端默认）
  // API 形式：chat_completion / messages / responses；undefined/空=未设置（litellm 自动路由）
  apiForm?: string
  // 请求级上下文长度（tokens）；undefined/0=未设置（跟随 registry 静态 max_context）
  contextLength?: number
  // /api/llm-config/test probe 得到的能力矩阵（camelCase JSON 的 capability 字段）。
  // undefined/null 均表示未探测（门禁放行，不误伤）。
  capability?: CapabilityMatrix | null
}

// 能力矩阵：/api/llm-config/test 返回的 probe 事实（snake_case 与后端响应一致）
export interface CapabilityMatrix {
  non_stream: boolean
  stream: boolean
  tool_call: boolean
  tool_followup: boolean
  json_output: boolean
  // probe 得到的最大输出 tokens（只读展示）；旧数据/后端未返回时缺省
  max_output?: number
}

// 模式门禁判定结果
export interface ModeGate {
  allowed: boolean
  reason: string
}

// 模式入口 capability 门禁（probe 事实驱动）。
// 规则：
//   - capability 为 null/undefined → 放行（probe 未完成不误伤）
//   - deep：tool_call===false → 禁用（深度 ReAct 依赖工具调用）
//           json_output===false → 仍允许，但 reason 标注管线结构化 caveat
//   - quick：stream 与 non_stream 均 false → 禁用（完全无法对话）
export function canEnterMode(mode: 'quick' | 'deep', capability: CapabilityMatrix | null | undefined): ModeGate {
  if (!capability) return { allowed: true, reason: '' }
  if (mode === 'deep') {
    if (capability.tool_call === false) {
      return { allowed: false, reason: '该 provider 不支持工具调用，可切换 provider 或使用快速模式' }
    }
    if (capability.json_output === false) {
      return { allowed: true, reason: '该 provider 不支持 JSON 输出，深度管线结构化步骤可能降级' }
    }
    return { allowed: true, reason: '' }
  }
  if (capability.stream === false && capability.non_stream === false) {
    return { allowed: false, reason: '该 provider 不支持流式与非流式对话，无法使用快速模式' }
  }
  return { allowed: true, reason: '' }
}

// 清除 capability（model/baseUrl/apiKey 变更后旧 probe 事实失效）
export function clearCapability(cfg: LLMConfig): LLMConfig {
  return { ...cfg, capability: null }
}

// 类型守卫：解析 localStorage / API 响应中的 capability 字段（字段缺失/类型不符 → null）
export function parseCapability(obj: unknown): CapabilityMatrix | null {
  if (typeof obj !== 'object' || obj === null) return null
  const c = obj as Record<string, unknown>
  const keys = ['non_stream', 'stream', 'tool_call', 'tool_followup', 'json_output'] as const
  if (!keys.every(k => typeof c[k] === 'boolean')) return null
  return {
    non_stream: c.non_stream as boolean,
    stream: c.stream as boolean,
    tool_call: c.tool_call as boolean,
    tool_followup: c.tool_followup as boolean,
    json_output: c.json_output as boolean,
    // max_output 为可选只读事实：仅在 number 时透传（缺失/类型不符 → undefined，不阻断矩阵解析）
    ...(typeof c.max_output === 'number' && Number.isFinite(c.max_output) ? { max_output: c.max_output } : {}),
  }
}

// probe warnings 机器码 → 人话映射（未知码透传）；键与后端 probes.py warnings 对齐
export const PROBE_WARNING_LABELS: Record<string, string> = {
  tool_call_probe_error: '工具调用请求被端点拒绝：请检查该 API 套餐/权限是否支持 tools 参数',
  tool_auto_no_call_forced_ok: '模型未主动调用工具，强制指定后通过（能力正常）',
  json_mode_unsupported: 'JSON 输出模式被端点拒绝，管线结构化节点可能降级',
  stream_unsupported: '流式输出被端点拒绝，快速模式体验可能降级',
}
export function formatProbeWarning(code: string): string {
  return PROBE_WARNING_LABELS[code] ?? code
}

// 矩阵呈现三态：通过 | 不支持（实测失败）| 未测（tool_followup 因 tool_call 未通过而未执行）
export type CapabilityItemState = 'pass' | 'fail' | 'untested'
export function capabilityItemState(key: keyof CapabilityMatrix, cap: CapabilityMatrix): CapabilityItemState {
  if (cap[key]) return 'pass'
  if (key === 'tool_followup' && !cap.tool_call) return 'untested'
  return 'fail'
}

// 后端 llm_config 请求载荷（camelCase，对齐后端 LLMConfigRequest 模型 baseUrl/apiKey）
export interface LLMConfigPayload {
  model?: string
  baseUrl?: string
  apiKey?: string
  thinking?: string
  apiForm?: string
  contextLength?: number
  /** probe 能力矩阵：仅供后端决定是否注册系统操作工具，不进入 LLMConfig */
  capability?: CapabilityMatrix | null
}

// API 形式选项（add-llm-api-form）：下拉框展示文案 + 存储值。
// 模型名前缀由所选 API 形式在后端推导，前端不再强制用户手写 openai/anthropic 前缀。
export const API_FORM_OPTIONS: { value: string; label: string }[] = [
  { value: 'chat_completion', label: 'OpenAI Chat Completion' },
  { value: 'messages', label: 'Anthropic Messages' },
  { value: 'responses', label: 'OpenAI Responses' },
]

// 缺省 API 形式：未显式配置时默认 OpenAI Chat Completion（无「跟随默认」空态）
export const DEFAULT_API_FORM = 'chat_completion'

// 合法 API 形式值集合（用于请求载荷过滤）
const API_FORM_VALUES = new Set(API_FORM_OPTIONS.map(o => o.value))

export const FA_LLM_CONFIG_KEY = 'fa_llm_config'
// 多配置管理：profiles 存储 key
export const FA_LLM_PROFILES_KEY = 'fa_llm_profiles'
// 旧版 API Key 的 localStorage key（迁移来源）
const LEGACY_API_KEY_STORAGE = 'fa_api_key'

// Provider 预设：选择后自动填充 model/baseUrl/thinking（静态常量，无后端依赖）
export interface ProviderPreset {
  name: string
  model: string
  baseUrl: string
  thinking: string // "" 表示该预设不展示思考开关（非 DeepSeek 模型）
  apiForm: string // API 形式值（chat_completion / messages / responses），"" 表示由用户选择
}

export const PROVIDER_PRESETS: ProviderPreset[] = [
  { name: 'DeepSeek 官方', model: 'deepseek/deepseek-chat', baseUrl: 'https://api.deepseek.com/v1', thinking: 'enabled', apiForm: 'chat_completion' },
  { name: 'OpenAI', model: 'openai/gpt-4o', baseUrl: '', thinking: '', apiForm: 'chat_completion' },
  { name: 'Anthropic', model: 'anthropic/claude-sonnet-4-20250514', baseUrl: '', thinking: '', apiForm: 'messages' },
  { name: '本地 Ollama', model: 'openai/llama3', baseUrl: 'http://localhost:11434/v1', thinking: '', apiForm: 'chat_completion' },
  { name: '自定义', model: '', baseUrl: '', thinking: 'enabled', apiForm: 'chat_completion' },
]

export const CUSTOM_PRESET_NAME = '自定义'

// 空配置（首次加载/未配置时的默认值）
export function emptyLlmConfig(): LLMConfig {
  return { apiKey: '', model: '', baseUrl: '', thinking: '', apiForm: DEFAULT_API_FORM }
}

// 从 localStorage 读取配置。
// 自动迁移：若 fa_llm_config 不存在但旧 key fa_api_key 存在，读取旧值为 apiKey 并清除旧 key。
export function loadLlmConfig(): LLMConfig {
  const raw = localStorage.getItem(FA_LLM_CONFIG_KEY)
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as Partial<LLMConfig>
      return {
        apiKey: typeof parsed.apiKey === 'string' ? parsed.apiKey : '',
        model: typeof parsed.model === 'string' ? parsed.model : '',
        baseUrl: typeof parsed.baseUrl === 'string' ? parsed.baseUrl : '',
        thinking: typeof parsed.thinking === 'string' ? parsed.thinking : '',
        // API 形式：合法值保留，缺省/非法回退默认 OpenAI Chat Completion
        apiForm: (typeof parsed.apiForm === 'string' && API_FORM_VALUES.has(parsed.apiForm) ? parsed.apiForm : DEFAULT_API_FORM),
        // 仅在 probe 事实有效时携带（undefined 与 null 语义等同：未探测）
        ...(parseCapability(parsed.capability) ? { capability: parseCapability(parsed.capability) } : {}),
      }
    } catch {
      // JSON 损坏：继续走迁移/默认分支
    }
  }
  // 迁移：旧 key 存在时，将其值作为 apiKey，写入新 key 并清除旧 key
  const legacy = localStorage.getItem(LEGACY_API_KEY_STORAGE)
  if (legacy) {
    const cfg: LLMConfig = { apiKey: legacy, model: '', baseUrl: '', thinking: '', apiForm: DEFAULT_API_FORM }
    localStorage.setItem(FA_LLM_CONFIG_KEY, JSON.stringify(cfg))
    localStorage.removeItem(LEGACY_API_KEY_STORAGE)
    return cfg
  }
  return emptyLlmConfig()
}

// 保存配置到 localStorage
export function saveLlmConfigToStorage(cfg: LLMConfig): void {
  localStorage.setItem(FA_LLM_CONFIG_KEY, JSON.stringify(cfg))
}

// 构建后端请求载荷（snake_case）。仅在有任意非空字段时返回对象，否则返回 null。
// 与 spec 场景对齐：「请求体 SHALL 包含 llm_config 字段，包含用户配置的非空字段」。
export function buildLlmConfigPayload(cfg: LLMConfig): LLMConfigPayload | null {
  const payload: LLMConfigPayload = {}
  // 防御：localStorage 数据（含旧版/手改/损坏）可能缺字段，避免 undefined.trim() 崩溃
  const model = (cfg.model ?? '').trim()
  const baseUrl = (cfg.baseUrl ?? '').trim()
  const apiKey = (cfg.apiKey ?? '').trim()
  if (model) payload.model = model
  if (baseUrl) payload.baseUrl = baseUrl
  if (apiKey) payload.apiKey = apiKey
  // thinking 仅在显式取值 enabled/disabled 时携带（空值回退后端默认）
  if (cfg.thinking === 'enabled' || cfg.thinking === 'disabled') payload.thinking = cfg.thinking
  // apiForm 仅显式合法值时携带（后端据此前缀推导 + 设 litellm api 参数）
  if (typeof cfg.apiForm === 'string' && API_FORM_VALUES.has(cfg.apiForm)) payload.apiForm = cfg.apiForm
  // contextLength 仅在正整数时携带（后端校验非法值 422）
  if (typeof cfg.contextLength === 'number' && Number.isInteger(cfg.contextLength) && cfg.contextLength > 0) {
    payload.contextLength = cfg.contextLength
  }
  // probe capability 随载荷透传：后端据此决定是否注册系统操作工具（不进入 LLMConfig）
  if (cfg.capability) payload.capability = cfg.capability
  // 全空（无任何真实配置字段）时返回 null —— apiForm 默认值本身不构成配置，
  // 否则仅下发 {apiForm} 会触发后端「缺 model」报错，破坏 env 回退路径。
  // capability 不算真实配置字段，但需要透传（无 key/model 时后端走 env）。
  if (!model && !baseUrl && !apiKey && payload.thinking === undefined && !payload.capability) return null
  return payload
}

// 判断模型名是否为 DeepSeek 系列（决定是否展示思考模式开关）
export function isDeepSeekModel(model: string): boolean {
  return model.toLowerCase().includes('deepseek')
}

// 根据当前 model/baseUrl/thinking 匹配预设名；无精确匹配返回"自定义"。
// 用于：用户手动修改输入框后，预设选择器自动切换为"自定义"。
export function matchPreset(cfg: { model: string; baseUrl: string; thinking: string }): string {
  const found = PROVIDER_PRESETS.find(
    (p) =>
      p.name !== CUSTOM_PRESET_NAME &&
      p.model === cfg.model &&
      p.baseUrl === cfg.baseUrl &&
      p.thinking === cfg.thinking,
  )
  return found ? found.name : CUSTOM_PRESET_NAME
}

// 与后端 resolver._KNOWN_PREFIXES 对齐（openai/deepseek/anthropic/gemini）；
// 域名推导出的前缀不在白名单时回退 openai/（OpenAI 兼容端点语义），
// 避免拼出后端解析必然拒绝的前缀（如 api.kimi.ai → kimi/）
const KNOWN_LLM_PREFIXES = new Set(['openai', 'deepseek', 'anthropic', 'gemini'])

// 为模型自动发现返回的原始模型名拼接 litellm 前缀。
// 规则（对齐 spec 场景）：
//   1. 原始值已含 '/' → 直接使用（已是 litellm 格式）
//   2. 否则 → 从 baseUrl 域名主体推导前缀（api.deepseek.com → deepseek），
//      推导结果不在白名单时回退 openai/
//   3. baseUrl 非法或为空 → 返回原始模型名（交由用户手动补全）
export function buildModelWithPrefix(rawModel: string, baseUrl: string): string {
  const model = rawModel.trim()
  if (!model) return model
  if (model.includes('/')) return model
  const trimmedBase = baseUrl.trim()
  if (!trimmedBase) return model
  try {
    const host = new URL(trimmedBase).hostname
    const parts = host.split('.').filter(Boolean)
    // 过滤常见非主体段（api/www/com/cn/org/net/io/ai/dev/co 等），取首个主体段作为前缀
    const insignificant = new Set(['api', 'www', 'com', 'cn', 'org', 'net', 'io', 'ai', 'dev', 'co'])
    const significant = parts.filter((p) => p && !insignificant.has(p.toLowerCase()))
    const prefix = significant[0] || parts[0] || 'openai'
    const safePrefix = KNOWN_LLM_PREFIXES.has(prefix.toLowerCase()) ? prefix.toLowerCase() : 'openai'
    return `${safePrefix}/${model}`
  } catch {
    // baseUrl 非法（如缺少协议）时无法推导前缀，返回原始模型名
    return model
  }
}

// ── 多配置管理（profiles）── add-custom-llm-api Decision 10（方案 A）──
// 多套命名 LLM 配置，存浏览器 localStorage（key: fa_llm_profiles），后端不存储。

// 单个 profile（命名配置）
export interface LLMProfile {
  id: string        // 唯一 ID（Date.now().toString()）
  name: string      // 用户命名，如「DeepSeek 办公」
  config: LLMConfig // { model, baseUrl, apiKey, thinking }
}

// localStorage 存储结构
export interface ProfileStore {
  profiles: LLMProfile[]
  activeId: string  // 当前激活 profile 的 id（空表示无激活，回退默认配置）
}

// 空的 profile store（首次加载/未配置时）
function emptyProfileStore(): ProfileStore {
  return { profiles: [], activeId: '' }
}

// 从 localStorage 读取 profiles。
// 自动迁移：若 fa_llm_profiles 不存在/为空但旧 key fa_llm_config 或 fa_api_key 存在，
// 迁移为第一个 profile（名称「旧配置」）并激活，迁移后清除旧 key。
export function loadProfiles(): ProfileStore {
  const raw = localStorage.getItem(FA_LLM_PROFILES_KEY)
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as Partial<ProfileStore>
      const profiles = Array.isArray(parsed.profiles) ? parsed.profiles.filter(isValidProfile) : []
      const activeId = typeof parsed.activeId === 'string' ? parsed.activeId : ''
      return { profiles, activeId }
    } catch {
      // JSON 损坏：继续走迁移/默认分支
    }
  }
  // 迁移：fa_llm_profiles 不存在时尝试从旧 key 迁移
  const legacyCfg = loadLlmConfig()
  const hasLegacy = legacyCfg.apiKey || legacyCfg.model || legacyCfg.baseUrl || legacyCfg.thinking
  if (hasLegacy) {
    const store = addProfile(emptyProfileStore(), '旧配置', legacyCfg)
    saveProfiles(store)
    // 迁移后清除旧 key
    localStorage.removeItem(FA_LLM_CONFIG_KEY)
    localStorage.removeItem(LEGACY_API_KEY_STORAGE)
    return store
  }
  return emptyProfileStore()
}

// 保存 profiles 到 localStorage
export function saveProfiles(store: ProfileStore): void {
  localStorage.setItem(FA_LLM_PROFILES_KEY, JSON.stringify(store))
}

// 添加新 profile（名称为空时不添加，返回原 store）
export function addProfile(store: ProfileStore, name: string, config: LLMConfig): ProfileStore {
  const trimmedName = name.trim()
  if (!trimmedName) return store
  const profile: LLMProfile = {
    id: Date.now().toString() + Math.random().toString(36).slice(2, 6),
    name: trimmedName,
    config: { ...config },
  }
  return {
    profiles: [...store.profiles, profile],
    activeId: profile.id, // 新增的 profile 自动设为激活
  }
}

// 删除 profile；若删除的是当前激活项，自动激活剩余第一个（无剩余则 activeId 为空）
export function deleteProfile(store: ProfileStore, id: string): ProfileStore {
  const profiles = store.profiles.filter((p) => p.id !== id)
  let activeId = store.activeId
  if (id === store.activeId) {
    activeId = profiles.length > 0 ? profiles[0].id : ''
  }
  return { profiles, activeId }
}

// 切换激活 profile（id 不存在时保持不变）
export function activateProfile(store: ProfileStore, id: string): ProfileStore {
  const exists = store.profiles.some((p) => p.id === id)
  if (!exists) return store
  return { ...store, activeId: id }
}

// 获取激活 profile 的 config；无 profile 或 activeId 无效时返回空配置或第一个 profile
export function getActiveConfig(store: ProfileStore): LLMConfig {
  if (store.profiles.length === 0) return emptyLlmConfig()
  const active = store.profiles.find((p) => p.id === store.activeId)
  if (active) return active.config
  // activeId 无效时回退到第一个 profile
  return store.profiles[0].config
}

// 获取激活 profile 的显示名称；无 profile 时返回「未配置」
export function getActiveProfileName(store: ProfileStore): string {
  if (store.profiles.length === 0) return '未配置'
  const active = store.profiles.find((p) => p.id === store.activeId)
  if (active) return active.name
  return store.profiles[0].name
}

// 类型守卫：检查对象是否为有效的 LLMProfile
function isValidProfile(obj: unknown): obj is LLMProfile {
  if (typeof obj !== 'object' || obj === null) return false
  const p = obj as Record<string, unknown>
  return typeof p.id === 'string' && typeof p.name === 'string' && typeof p.config === 'object'
}
