// LlmConfigPane LLM 配置分区（add-agent-settings-center Task 7；refine-llm-settings-pane 重写渲染层）
// 自 App.tsx 的 SettingsModal 抽取（编辑态 state、provider 预设、模型发现、连通性测试、profile 管理），
// 去掉 open/onClose 与外层 Dialog 壳，由设置中心页（SettingsCenterPage）内嵌渲染。
// 实现 delta 5.1/5.5（模型/BaseURL/思考开关）、6.1-6.5（Provider 预设 + 模型发现）、7.1-7.4（连通性测试）。
// refine-llm-settings-pane：按「连接 / 模型 / 高级」三段分组；API Key 只写（已保存密钥永不回填）；
// Base URL 与上下文长度为字段级校验（非法时红字且阻止保存）；最大输出只读展示 probe 的 max_output。
import { useEffect, useState } from 'react'
import { Button } from '../../../components/ui/button'
import { Input } from '../../../components/ui/input'
import {
  PROVIDER_PRESETS,
  API_FORM_OPTIONS,
  DEFAULT_API_FORM,
  isDeepSeekModel,
  matchPreset,
  buildModelWithPrefix,
  parseCapability,
  capabilityItemState,
  formatProbeWarning,
  type LLMConfig,
  type CapabilityMatrix,
  type ProfileStore,
} from '../../../llmConfig'

// 字段级校验错误：仅在点「确认 / 另存为」时计算，输入变更即清除该字段错误
interface FieldErrors {
  baseUrl?: string
  contextLength?: string
}

export function LlmConfigPane({ config, backendDefaults, profileStore, capability: capabilityProp, onProbeCapability, onSave, onSaveAs, onSwitchProfile, onDeleteProfile }: {
  config: LLMConfig
  backendDefaults: { model: string; baseUrl: string; thinking: string }
  profileStore: ProfileStore
  capability: CapabilityMatrix | null
  onProbeCapability: (cap: CapabilityMatrix | null) => void
  onSave: (cfg: LLMConfig) => void
  onSaveAs: (cfg: LLMConfig, name: string) => void
  onSwitchProfile: (id: string) => void
  onDeleteProfile: (id: string) => void
}) {
  // 本地编辑态：确认时才回写父级并持久化（避免每次按键都写 localStorage）
  // API Key 为只写字段：初值恒为 ''，绝不把 config.apiKey 放进 value（已保存密钥只用于「是否已配置」判断与保存兜底）
  const [apiKey, setApiKey] = useState('')
  // 密钥是否以明文展示（眼睛按钮切换）
  const [showApiKey, setShowApiKey] = useState(false)
  const [model, setModel] = useState(config.model)
  const [baseUrl, setBaseUrl] = useState(config.baseUrl)
  // 思考模式：已保存值优先，其次后端默认，最后内置 enabled
  const [thinking, setThinking] = useState<string>(config.thinking || backendDefaults.thinking || 'enabled')
  const [apiForm, setApiForm] = useState<string>(config.apiForm || DEFAULT_API_FORM)
  // 上下文长度（tokens）：空串=未设置（跟随 registry 静态 max_context）
  const [contextLength, setContextLength] = useState<string>(config.contextLength ? String(config.contextLength) : '')
  // 字段级校验错误（红字就地提示）
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  // 配置管理：另存为输入（delta Decision 10）
  const [profileName, setProfileName] = useState('')

  // 模型自动发现状态
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([])
  const [discoveryLoading, setDiscoveryLoading] = useState(false)
  const [discoveryMsg, setDiscoveryMsg] = useState<{ text: string; ok: boolean } | null>(null)

  // 连通性测试状态
  const [testStatus, setTestStatus] = useState<'idle' | 'loading' | 'success' | 'fail'>('idle')
  const [testLatencyMs, setTestLatencyMs] = useState<number | undefined>(undefined)
  const [testMessage, setTestMessage] = useState('')
  // probe 得到的能力矩阵（分区内展示；连接三要素变更后置空待重探测）
  const [capability, setCapability] = useState<CapabilityMatrix | null>(capabilityProp)
  const [testWarnings, setTestWarnings] = useState<string[]>([])

  // 切换 profile 时表单整体切换（设计档案 §15 原子切换，ZCode 式编辑逻辑）：
  // 本地 useState 只在挂载取初值，activeId 变化后必须显式同步为目标 profile 的
  // 配置，否则分区停留在旧 profile 的字段。密钥为只写字段：切换后清空输入并回到掩码，
  // 仅由 placeholder / 状态点反映目标 profile 是否已保存密钥。
  useEffect(() => {
    const active = profileStore.profiles.find(p => p.id === profileStore.activeId)
    if (!active) return
    setApiKey('')
    setShowApiKey(false)
    setModel(active.config.model)
    setBaseUrl(active.config.baseUrl)
    setThinking(active.config.thinking || backendDefaults.thinking || 'enabled')
    setApiForm(active.config.apiForm || DEFAULT_API_FORM)
    setContextLength(active.config.contextLength ? String(active.config.contextLength) : '')
    setFieldErrors({})
    setCapability(active.config.capability ?? null)
    setTestWarnings([])
    setTestStatus('idle')
    setTestLatencyMs(undefined)
    setTestMessage('')
    // 模型发现结果属于旧 profile 的端点，切换后一并清空避免误导
    setDiscoveredModels([])
    setDiscoveryMsg(null)
    setProfileName('')
    // eslint-disable-next-line react-hooks/exhaustive-deps -- 仅在激活 profile 切换时同步；config 对象因保存/probe 更新会变 identity，不应重置用户编辑中字段
  }, [profileStore.activeId])

  // 连接三要素（apiKey/model/baseUrl）变更 → 旧 probe 事实失效
  const invalidateCapability = () => {
    setCapability(null)
    setTestWarnings([])
  }

  // 实际使用的密钥：只写输入框有值则用新值，否则复用已保存密钥；两者都空为空串。
  // 测试连接 / 模型发现也要用该值，避免「已保存密钥」因不回显而在请求中丢失。
  const resolvedApiKey = apiKey.trim() || config.apiKey || ''

  // 思考模式开关仅在 DeepSeek 模型下展示（delta 5.5）
  const showThinkingToggle = isDeepSeekModel(model)
  // 当前值匹配的预设名（手动修改后自动回退为"自定义"）
  const currentPreset = matchPreset({ model, baseUrl, thinking: showThinkingToggle ? thinking : '' })

  // 选择预设：自动填充 model/baseUrl/thinking（不触发保存）
  const applyPreset = (name: string) => {
    const preset = PROVIDER_PRESETS.find((p) => p.name === name)
    if (!preset) return
    setModel(preset.model)
    setBaseUrl(preset.baseUrl)
    setThinking(preset.thinking || 'enabled')
    setApiForm(preset.apiForm)
    invalidateCapability()
  }

  // 刷新模型列表：调用后端代理拉取 {base_url}/models（delta 6.3）
  const refreshModels = async () => {
    if (discoveryLoading) return
    setDiscoveryLoading(true)
    setDiscoveryMsg(null)
    try {
      const resp = await fetch('/api/llm-config/models', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // 字段名 camelCase 与后端 ModelsRequest(baseUrl/apiKey) 一致，否则被 Pydantic 忽略回退环境变量；
        // 密钥走只写兜底：未输入新值时复用已保存密钥（空串则由后端回退环境变量）
        body: JSON.stringify({ baseUrl: baseUrl.trim(), apiKey: resolvedApiKey }),
      })
      const data: { models?: unknown[]; error?: string } = await resp.json().catch(() => ({}))
      const rawModels = Array.isArray(data?.models) ? data.models : []
      const models = rawModels.filter((m): m is string => typeof m === 'string')
      setDiscoveredModels(models)
      if (models.length === 0) {
        setDiscoveryMsg({ text: data?.error || '该端点不支持模型自动发现，请手动输入', ok: false })
      } else {
        setDiscoveryMsg({ text: `发现 ${models.length} 个可用模型`, ok: true })
      }
    } catch {
      setDiscoveredModels([])
      setDiscoveryMsg({ text: '连接超时，请检查 Base URL', ok: false })
    } finally {
      setDiscoveryLoading(false)
    }
  }

  // 从下拉选择模型：拼接 litellm 前缀填入 model 输入框（delta 6.4）
  const pickDiscoveredModel = (raw: string) => {
    setModel(buildModelWithPrefix(raw, baseUrl))
  }

  // 测试连接：后端发送极简 LLM 请求验证配置（delta 7.1）
  const testConnection = async () => {
    if (testStatus === 'loading') return
    setTestStatus('loading')
    setTestMessage('')
    setTestLatencyMs(undefined)
    try {
      const resp = await fetch('/api/llm-config/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // 字段名 camelCase 与后端 LLMConfigRequest(baseUrl/apiKey) 一致，否则被 Pydantic 忽略回退环境变量
        body: JSON.stringify({
          model: model.trim(),
          baseUrl: baseUrl.trim(),
          apiKey: resolvedApiKey,
          thinking: showThinkingToggle ? thinking : '',
        }),
      })
      // 后端响应为 camelCase（latencyMs/errorType/capability/warnings）；旧代码误读 snake_case 导致延迟不显示
      const data: { success?: boolean; latencyMs?: number; model?: string; error?: string; errorType?: string; capability?: unknown; warnings?: unknown } = await resp.json().catch(() => ({}))
      if (data?.success) {
        setTestStatus('success')
        setTestLatencyMs(typeof data.latencyMs === 'number' ? data.latencyMs : undefined)
        setTestMessage(typeof data.model === 'string' ? data.model : '')
        // probe 事实：capability 矩阵 + warnings（成功但无 capability 视为未探测）
        const cap = parseCapability(data.capability)
        setCapability(cap)
        setTestWarnings(Array.isArray(data.warnings) ? data.warnings.filter((w): w is string => typeof w === 'string') : [])
        onProbeCapability(cap)
      } else {
        setTestStatus('fail')
        setTestMessage(formatTestError(data?.errorType, data?.error))
      }
    } catch {
      setTestStatus('fail')
      setTestMessage('请求失败，请检查网络或后端服务')
    }
  }

  // 字段级校验：Base URL 非空必须 http(s)://；上下文长度非空必须正整数。
  // 返回 true 表示可保存；错误同时写入 fieldErrors，由字段下方红字呈现。
  const validate = (): boolean => {
    const next: FieldErrors = {}
    if (baseUrl.trim() !== '' && !/^https?:\/\//.test(baseUrl.trim())) {
      next.baseUrl = 'API Base URL 必须以 http:// 或 https:// 开头'
    }
    if (contextLength.trim() !== '') {
      const parsed = Number(contextLength)
      if (!Number.isInteger(parsed) || parsed <= 0) {
        next.contextLength = '上下文长度必须为正整数'
      }
    }
    setFieldErrors(next)
    return Object.keys(next).length === 0
  }

  // 当前表单值 → LLMConfig（确认与另存为共用；非法字段已被 validate 拦截）
  const buildConfig = (): LLMConfig => {
    const parsedContextLength = Number(contextLength)
    const hasValidContextLength = contextLength.trim() !== '' && Number.isInteger(parsedContextLength) && parsedContextLength > 0
    return {
      apiKey: resolvedApiKey,
      model: model.trim(),
      baseUrl: baseUrl.trim(),
      // 非 DeepSeek 模型不持久化 thinking（开关已隐藏）
      thinking: showThinkingToggle ? thinking : '',
      apiForm: apiForm,
      contextLength: hasValidContextLength ? parsedContextLength : undefined,
      // 携带当前 probe 事实（连接三要素未变时保留；变更则由父级 clearCapability 清空）
      capability,
    }
  }

  const handleSave = () => {
    if (!validate()) return
    onSave(buildConfig())
  }

  const handleSaveAs = () => {
    const name = profileName.trim()
    if (!name) return
    if (!validate()) return
    // 将当前表单值另存为新 profile（密钥同样走「未输入保留原值」语义）
    onSaveAs(buildConfig(), name)
    setProfileName('')
  }

  return (
    <div data-testid="llm-config-pane" className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold mb-1" style={{ color: 'var(--text-default)' }}>LLM 配置</h2>
        <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>配置模型、API 端点与密钥。配置保存在浏览器本地，刷新页面不会丢失。</p>
      </div>

      {/* ── 连接段：Provider 预设、Base URL、API Key、连通性测试 ── */}
      <section
        className="rounded-xl p-4 space-y-4"
        style={{ background: 'var(--bg-overlay-l1)', border: '1px solid var(--border-neutral-l1)' }}
        aria-labelledby="llm-section-connection"
      >
        <h3 id="llm-section-connection" className="text-sm font-semibold flex items-center gap-1.5" style={{ color: 'var(--text-default)' }}>
          <i className="fas fa-plug text-xs" style={{ color: 'var(--text-tertiary)' }}></i>连接
        </h3>

        {/* Provider 预设卡片（delta 6.1）：2 列小卡片，选中高亮 */}
        <div>
          <label className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>Provider 预设</label>
          <div className="grid grid-cols-2 gap-2" role="group" aria-label="Provider 预设">
            {PROVIDER_PRESETS.map(p => {
              const selected = currentPreset === p.name
              return (
                <button
                  key={p.name}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => applyPreset(p.name)}
                  className="rounded-lg px-3 py-2 text-left text-xs transition-colors"
                  style={{
                    background: selected ? 'var(--bg-overlay-l3)' : 'transparent',
                    border: `1px solid ${selected ? 'var(--text-brand)' : 'var(--border-neutral-l1)'}`,
                    color: 'var(--text-default)',
                  }}
                >
                  <span className="block font-medium">{p.name}</span>
                  <span className="block text-[10px] truncate" style={{ color: 'var(--text-tertiary)' }}>
                    {p.model || '手动填写模型'}
                  </span>
                </button>
              )
            })}
          </div>
        </div>

        {/* API Base URL（delta 5.1）：非空必须 http(s):// */}
        <div>
          <label htmlFor="llm-base-url" className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>API Base URL</label>
          <Input
            id="llm-base-url"
            data-testid="base-url-input"
            type="text"
            placeholder={backendDefaults.baseUrl || 'https://api.deepseek.com/v1（留空使用默认）'}
            value={baseUrl}
            aria-invalid={Boolean(fieldErrors.baseUrl)}
            onChange={e => {
              setBaseUrl(e.target.value)
              invalidateCapability()
              if (fieldErrors.baseUrl) setFieldErrors(prev => ({ ...prev, baseUrl: undefined }))
            }}
            style={{ color: 'var(--text-default)' }}
          />
          {fieldErrors.baseUrl && (
            <p data-testid="base-url-error" className="text-xs mt-1" style={{ color: 'var(--status-error-default)' }}>
              <i className="fas fa-exclamation-circle mr-1"></i>{fieldErrors.baseUrl}
            </p>
          )}
        </div>

        {/* API Key：只写语义（已保存时输入框为空、仅提示「已保存」；输入新值即替换） */}
        <div>
          <label htmlFor="llm-api-key" className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>API Key</label>
          <div className="flex items-center gap-2">
            {/* 状态点：绿=已配置密钥，灰=未配置 */}
            <span
              data-testid="api-key-status-dot"
              data-state={config.apiKey ? 'saved' : 'empty'}
              aria-hidden="true"
              className="inline-block h-2 w-2 shrink-0 rounded-full"
              style={{ background: config.apiKey ? 'var(--status-success-default)' : 'var(--text-tertiary)' }}
            />
            <div className="relative flex-1">
              <Input
                id="llm-api-key"
                data-testid="api-key-input"
                type={showApiKey ? 'text' : 'password'}
                placeholder={config.apiKey ? '已保存（不回显）— 输入新值以替换' : 'sk-...'}
                value={apiKey}
                autoComplete="off"
                onChange={e => { setApiKey(e.target.value); invalidateCapability() }}
                className="pr-10"
                style={{ color: 'var(--text-default)' }}
              />
              <button
                type="button"
                onClick={() => setShowApiKey(v => !v)}
                aria-label={showApiKey ? '隐藏密钥' : '显示密钥'}
                title={showApiKey ? '隐藏密钥' : '显示密钥'}
                className="absolute right-1 top-1/2 -translate-y-1/2 h-7 w-7 inline-flex items-center justify-center rounded-md"
                style={{ color: 'var(--text-tertiary)' }}
              >
                <i className={`fas ${showApiKey ? 'fa-eye-slash' : 'fa-eye'} text-xs`}></i>
              </button>
            </div>
          </div>
          <p className="text-[11px] mt-1" style={{ color: 'var(--text-tertiary)' }}>
            {config.apiKey
              ? '已保存密钥不会回显；留空保存则保留原密钥。'
              : '密钥仅保存在浏览器本地，不会上传到分析服务。'}
          </p>
        </div>

        {/* 连通性测试（delta 7.1-7.4） */}
        <div>
          <Button
            variant="secondary"
            onClick={testConnection}
            disabled={testStatus === 'loading'}
            className="w-full"
          >
            <i className={`fas ${testStatus === 'loading' ? 'fa-spinner fa-spin' : 'fa-plug'} mr-1.5`}></i>
            {testStatus === 'loading' ? '测试中…' : '测试连接'}
          </Button>
          {testStatus === 'success' && (
            <p className="text-xs mt-2" style={{ color: 'var(--status-success-default)' }}>
              <i className="fas fa-check-circle mr-1"></i>
              连接成功{typeof testLatencyMs === 'number' ? ` · ${testLatencyMs}ms` : ''}{testMessage ? ` · ${testMessage}` : ''}
            </p>
          )}
          {testStatus === 'fail' && (
            <p className="text-xs mt-2" style={{ color: 'var(--status-error-default)' }}>
              <i className="fas fa-times-circle mr-1"></i>
              {testMessage}
            </p>
          )}

          {/* 能力矩阵（harden-llm-gateway Task 6：probe 事实驱动展示） */}
          <div className="mt-3 rounded-lg px-3 py-2.5" style={{ background: 'var(--bg-overlay-l2)' }}>
            <div className="text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>
              <i className="fas fa-clipboard-check mr-1"></i>能力矩阵
            </div>
            {capability ? (
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5" data-testid="capability-matrix">
                {(
                  [
                    { key: 'non_stream', label: '非流式' },
                    { key: 'stream', label: '流式' },
                    { key: 'tool_call', label: '工具调用' },
                    { key: 'tool_followup', label: '工具跟随' },
                    { key: 'json_output', label: 'JSON 输出' },
                  ] as { key: keyof CapabilityMatrix; label: string }[]
                ).map(item => {
                  // 三态：通过 / 不支持（实测失败）/ 未测（tool_followup 因 tool_call 未通过而未执行）
                  const state = capabilityItemState(item.key, capability)
                  return (
                    <div key={item.key} className="flex items-center gap-1.5 text-xs" data-testid={`capability-${item.key}`} data-state={state}>
                      <i
                        className={`fas ${state === 'pass' ? 'fa-check-circle' : state === 'fail' ? 'fa-times-circle' : 'fa-minus-circle'}`}
                        style={{
                          color:
                            state === 'pass'
                              ? 'var(--status-success-default)'
                              : state === 'fail'
                                ? 'var(--status-error-default)'
                                : 'var(--text-tertiary)',
                        }}
                      ></i>
                      <span style={{ color: 'var(--text-secondary)' }}>
                        {item.label}
                        {state !== 'pass' && (
                          <span style={{ color: 'var(--text-tertiary)' }}>{state === 'fail' ? '（不支持）' : '（未测）'}</span>
                        )}
                      </span>
                    </div>
                  )
                })}
              </div>
            ) : (
              // probe_required：未探测时提示（不展示错误，仅静态能力语义）
              <p className="text-[11px]" data-testid="capability-probe-required" style={{ color: 'var(--text-tertiary)' }}>
                <i className="fas fa-info-circle mr-1"></i>未探测，展示静态能力。点击「测试连接」获取该 provider 的实测能力矩阵。
              </p>
            )}
            {testWarnings.length > 0 && (
              <ul className="mt-2 space-y-1" data-testid="capability-warnings">
                {testWarnings.map((w, i) => (
                  <li key={i} className="text-[11px]" style={{ color: 'var(--status-warning-default)' }}>
                    <i className="fas fa-exclamation-triangle mr-1"></i>{formatProbeWarning(w)}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </section>

      {/* ── 模型段：模型名称与发现、上下文窗口、最大输出、思考模式 ── */}
      <section
        className="rounded-xl p-4 space-y-4"
        style={{ background: 'var(--bg-overlay-l1)', border: '1px solid var(--border-neutral-l1)' }}
        aria-labelledby="llm-section-model"
      >
        <h3 id="llm-section-model" className="text-sm font-semibold flex items-center gap-1.5" style={{ color: 'var(--text-default)' }}>
          <i className="fas fa-microchip text-xs" style={{ color: 'var(--text-tertiary)' }}></i>模型
        </h3>

        {/* 模型名称 + 刷新按钮（delta 6.3） */}
        <div>
          <label htmlFor="llm-model-input" className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>模型名称</label>
          <div className="flex gap-2">
            <Input
              id="llm-model-input"
              type="text"
              placeholder={backendDefaults.model || 'deepseek/deepseek-chat'}
              value={model}
              onChange={e => { setModel(e.target.value); invalidateCapability() }}
              className="flex-1"
              style={{ color: 'var(--text-default)' }}
            />
            <Button
              variant="secondary"
              size="sm"
              onClick={refreshModels}
              disabled={discoveryLoading}
              className="whitespace-nowrap"
              title="从 Base URL 拉取可用模型列表"
            >
              <i className={`fas fa-sync-alt mr-1 ${discoveryLoading ? 'fa-spin' : ''}`}></i>
              {discoveryLoading ? '加载中' : '刷新模型'}
            </Button>
          </div>
        </div>

        {/* 自动发现的模型下拉（delta 6.4） */}
        {discoveredModels.length > 0 && (
          <select
            value=""
            onChange={e => { if (e.target.value) pickDiscoveredModel(e.target.value) }}
            className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm outline-none"
            style={{ color: 'var(--text-default)' }}
            aria-label="从发现列表选择模型"
          >
            <option value="">从发现列表选择模型…</option>
            {discoveredModels.map(m => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        )}
        {/* 模型发现提示（delta 6.5：失败/空列表提示但不阻塞手动输入） */}
        {discoveryMsg && (
          <p className="text-xs" style={{ color: discoveryMsg.ok ? 'var(--status-success-default)' : 'var(--status-error-default)' }}>
            <i className={`fas ${discoveryMsg.ok ? 'fa-check-circle' : 'fa-exclamation-circle'} mr-1`}></i>
            {discoveryMsg.text}
          </p>
        )}
        {discoveredModels.length === 0 && !discoveryMsg && (
          <p className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>
            <i className="fas fa-info-circle mr-1"></i>
            litellm 格式：provider/model，如 deepseek/deepseek-chat
          </p>
        )}

        {/* 上下文窗口 / 最大输出：并排展示，前者可编辑（正整数），后者只读（probe 事实） */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label htmlFor="llm-context-length" className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>上下文窗口（tokens）</label>
            <Input
              id="llm-context-length"
              data-testid="context-length-input"
              type="text"
              inputMode="numeric"
              placeholder="留空跟随默认（如 128000）"
              value={contextLength}
              aria-invalid={Boolean(fieldErrors.contextLength)}
              onChange={e => {
                setContextLength(e.target.value)
                if (fieldErrors.contextLength) setFieldErrors(prev => ({ ...prev, contextLength: undefined }))
              }}
              style={{ color: 'var(--text-default)' }}
            />
            {fieldErrors.contextLength && (
              <p data-testid="context-length-error" className="text-xs mt-1" style={{ color: 'var(--status-error-default)' }}>
                <i className="fas fa-exclamation-circle mr-1"></i>{fieldErrors.contextLength}
              </p>
            )}
          </div>
          <div>
            <span className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>最大输出</span>
            <div
              data-testid="max-output-value"
              className="flex h-9 items-center rounded-md px-3 text-sm"
              style={{
                background: 'var(--bg-overlay-l2)',
                border: '1px solid var(--border-neutral-l1)',
                color: typeof capability?.max_output === 'number' ? 'var(--text-default)' : 'var(--text-tertiary)',
              }}
            >
              {typeof capability?.max_output === 'number' ? `${capability.max_output} tokens` : '测试连接后探测'}
            </div>
            <p className="text-[11px] mt-1" style={{ color: 'var(--text-tertiary)' }}>由连通性探测得到，只读。</p>
          </div>
        </div>

        {/* 思考模式开关（delta 5.5：仅 DeepSeek 模型展示） */}
        {showThinkingToggle && (
          <div className="flex items-center justify-between rounded-lg px-4 py-3" style={{ background: 'var(--bg-overlay-l2)' }}>
            <div>
              <div className="text-sm font-medium" style={{ color: 'var(--text-default)' }}>思考模式</div>
              <div className="text-[11px]" style={{ color: 'var(--text-tertiary)' }}>DeepSeek 深度推理（enabled / disabled）</div>
            </div>
            <button
              onClick={() => setThinking(thinking === 'enabled' ? 'disabled' : 'enabled')}
              className="relative inline-flex h-6 w-11 items-center rounded-full transition-colors"
              style={{ background: thinking === 'enabled' ? 'var(--bg-brand)' : 'var(--bg-overlay-l3)' }}
              role="switch"
              aria-checked={thinking === 'enabled'}
              aria-label="思考模式"
            >
              <span
                className="inline-block h-4 w-4 transform rounded-full bg-white transition-transform"
                style={{ transform: thinking === 'enabled' ? 'translateX(24px)' : 'translateX(4px)' }}
              />
            </button>
          </div>
        )}
      </section>

      {/* ── 高级段：折叠容纳 API 形式与配置管理 ── */}
      <details
        data-testid="llm-advanced-section"
        className="rounded-xl p-4"
        style={{ background: 'var(--bg-overlay-l1)', border: '1px solid var(--border-neutral-l1)' }}
      >
        <summary className="flex cursor-pointer items-center gap-1.5" style={{ color: 'var(--text-default)' }}>
          <i className="fas fa-sliders-h text-xs" style={{ color: 'var(--text-tertiary)' }}></i>
          <h3 className="m-0 inline text-sm font-semibold">高级</h3>
          <span className="ml-auto text-[11px]" style={{ color: 'var(--text-tertiary)' }}>API 形式与配置管理</span>
        </summary>

        <div className="mt-4 space-y-4">
          {/* API 形式（add-llm-api-form）：显式指定协议，模型名前缀据此推导 */}
          <div>
            <label htmlFor="llm-api-form" className="block text-xs font-medium mb-1.5" style={{ color: 'var(--text-secondary)' }}>API 形式</label>
            <select
              id="llm-api-form"
              value={apiForm}
              onChange={e => setApiForm(e.target.value)}
              className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm outline-none"
              style={{ color: 'var(--text-default)' }}
            >
              {API_FORM_OPTIONS.map(o => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>

          {/* 配置管理区（delta Decision 10） */}
          <div className="pt-4" style={{ borderTop: '1px solid var(--border-neutral-l1)' }}>
            <label className="block text-xs font-medium mb-2" style={{ color: 'var(--text-secondary)' }}>
              <i className="fas fa-bookmark mr-1"></i>配置管理
            </label>

            {/* 另存为新 profile */}
            <div className="flex gap-2 mb-3">
              <Input
                type="text"
                placeholder="输入配置名称，如「DeepSeek 办公」"
                value={profileName}
                onChange={e => setProfileName(e.target.value)}
                className="flex-1 h-8 text-xs"
                style={{ color: 'var(--text-default)' }}
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={handleSaveAs}
                className="whitespace-nowrap"
              >
                <i className="fas fa-save mr-1"></i>另存为
              </Button>
            </div>

            {/* 已有 profile 列表 */}
            {profileStore.profiles.length > 0 && (
              <div className="space-y-1">
                {profileStore.profiles.map(p => (
                  <div
                    key={p.id}
                    className="flex items-center justify-between px-3 py-1.5 rounded-lg text-xs"
                    style={{
                      background: p.id === profileStore.activeId ? 'var(--bg-overlay-l2)' : 'transparent',
                      color: 'var(--text-secondary)',
                    }}
                  >
                    <button
                      onClick={() => onSwitchProfile(p.id)}
                      className="flex items-center gap-1.5 flex-1 text-left"
                    >
                      {p.id === profileStore.activeId && <i className="fas fa-check text-[10px]" style={{ color: 'var(--text-brand)' }}></i>}
                      <span>{p.name}</span>
                    </button>
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => onDeleteProfile(p.id)}
                      className="h-5 w-5 text-destructive opacity-60 hover:opacity-100 hover:text-destructive"
                      aria-label={`删除配置 ${p.name}`}
                    >
                      <i className="fas fa-trash-alt text-[10px]"></i>
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </details>

      <Button onClick={handleSave} className="w-full">
        确认
      </Button>
    </div>
  )
}

// 连通性测试失败提示：按 error_type 展示针对性文案（delta 7.3）
function formatTestError(errorType: string | undefined, error: string | undefined): string {
  switch (errorType) {
    case 'auth':
      return 'API Key 无效，请检查密钥配置'
    case 'network':
      return '无法连接到 API 端点，请检查 Base URL'
    case 'model_not_found':
      return '模型不存在，请检查模型名称'
    case 'model_prefix_invalid':
      return '模型名前缀不被支持：OpenAI 兼容端点请使用 openai/<模型名>'
    default:
      return error || '测试失败，请检查配置'
  }
}
