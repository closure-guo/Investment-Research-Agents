import { describe, it, expect, beforeEach } from 'vitest'
import {
  FA_LLM_CONFIG_KEY,
  FA_LLM_PROFILES_KEY,
  emptyLlmConfig,
  loadLlmConfig,
  saveLlmConfigToStorage,
  buildLlmConfigPayload,
  isDeepSeekModel,
  matchPreset,
  buildModelWithPrefix,
  PROVIDER_PRESETS,
  CUSTOM_PRESET_NAME,
  loadProfiles,
  saveProfiles,
  addProfile,
  deleteProfile,
  getActiveConfig,
  activateProfile,
  getActiveProfileName,
  type LLMConfig,
  type LLMProfile,
  type ProfileStore,
} from '../llmConfig'

// LLM 配置纯逻辑测试（add-custom-llm-api Task 5.2/5.4/6.2/6.4）
// 覆盖：localStorage 读写、旧 key 迁移、损坏 JSON 回退、载荷构建（snake_case/非空过滤）、
// DeepSeek 判定、预设匹配、litellm 前缀推导。

describe('loadLlmConfig - localStorage 读取与迁移', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('无任何配置时返回空配置', () => {
    const cfg = loadLlmConfig()
    expect(cfg).toEqual({ apiKey: '', model: '', baseUrl: '', thinking: '', apiForm: 'chat_completion' })
  })

  it('读取已存在的 fa_llm_config JSON', () => {
    const stored: LLMConfig = { apiKey: 'sk-1', model: 'deepseek/deepseek-chat', baseUrl: 'https://api.deepseek.com/v1', thinking: 'enabled' }
    localStorage.setItem(FA_LLM_CONFIG_KEY, JSON.stringify(stored))
    expect(loadLlmConfig()).toEqual({ ...stored, apiForm: 'chat_completion' })
  })

  it('旧 key fa_api_key 存在且无新 key 时自动迁移', () => {
    localStorage.setItem('fa_api_key', 'sk-legacy')
    const cfg = loadLlmConfig()
    expect(cfg.apiKey).toBe('sk-legacy')
    expect(cfg.model).toBe('')
    // 迁移后新 key 已写入、旧 key 已清除
    expect(localStorage.getItem(FA_LLM_CONFIG_KEY)).toBeTruthy()
    expect(localStorage.getItem('fa_api_key')).toBeNull()
  })

  it('新 key 存在时即使旧 key 也在也不读旧 key（新 key 优先）', () => {
    const stored: LLMConfig = { apiKey: 'sk-new', model: 'openai/gpt-4o', baseUrl: '', thinking: '' }
    localStorage.setItem(FA_LLM_CONFIG_KEY, JSON.stringify(stored))
    localStorage.setItem('fa_api_key', 'sk-legacy')
    const cfg = loadLlmConfig()
    expect(cfg.apiKey).toBe('sk-new')
    expect(cfg.model).toBe('openai/gpt-4o')
    // 新 key 存在时不触发迁移，旧 key 保持原状
    expect(localStorage.getItem('fa_api_key')).toBe('sk-legacy')
  })

  it('fa_llm_config 为损坏 JSON 时回退并尝试迁移旧 key', () => {
    localStorage.setItem(FA_LLM_CONFIG_KEY, '{not-json')
    localStorage.setItem('fa_api_key', 'sk-fallback')
    const cfg = loadLlmConfig()
    expect(cfg.apiKey).toBe('sk-fallback')
  })

  it('JSON 缺字段时以空字符串兜底（不崩溃）', () => {
    localStorage.setItem(FA_LLM_CONFIG_KEY, JSON.stringify({ apiKey: 'sk-x' }))
    const cfg = loadLlmConfig()
    expect(cfg.apiKey).toBe('sk-x')
    expect(cfg.model).toBe('')
    expect(cfg.baseUrl).toBe('')
    expect(cfg.thinking).toBe('')
  })
})

describe('saveLlmConfigToStorage', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('序列化为 JSON 存入 fa_llm_config', () => {
    const cfg: LLMConfig = { apiKey: 'sk-1', model: 'm', baseUrl: 'b', thinking: 'disabled' }
    saveLlmConfigToStorage(cfg)
    expect(JSON.parse(localStorage.getItem(FA_LLM_CONFIG_KEY)!)).toEqual(cfg)
  })
})

describe('buildLlmConfigPayload - 载荷构建', () => {
  it('全空配置返回 null（不携带 llm_config 字段）', () => {
    expect(buildLlmConfigPayload(emptyLlmConfig())).toBeNull()
  })

  it('输出 camelCase 键名（baseUrl / apiKey，对齐后端 LLMConfigRequest）', () => {
    const cfg: LLMConfig = { apiKey: 'sk-1', model: 'openai/gpt-4o', baseUrl: 'https://x/v1', thinking: 'enabled' }
    expect(buildLlmConfigPayload(cfg)).toEqual({
      model: 'openai/gpt-4o',
      baseUrl: 'https://x/v1',
      apiKey: 'sk-1',
      thinking: 'enabled',
    })
  })

  it('仅含部分字段时只输出非空字段（其余回退后端默认）', () => {
    const cfg: LLMConfig = { apiKey: '', model: 'openai/gpt-4o', baseUrl: '', thinking: '' }
    expect(buildLlmConfigPayload(cfg)).toEqual({ model: 'openai/gpt-4o' })
  })

  it('字段前后空白被裁剪', () => {
    const cfg: LLMConfig = { apiKey: '  sk-1  ', model: '  m  ', baseUrl: '  b  ', thinking: 'enabled' }
    expect(buildLlmConfigPayload(cfg)).toEqual({ model: 'm', baseUrl: 'b', apiKey: 'sk-1', thinking: 'enabled' })
  })

  it('thinking 为空字符串时不携带（回退后端默认）', () => {
    const cfg: LLMConfig = { apiKey: 'sk-1', model: '', baseUrl: '', thinking: '' }
    expect(buildLlmConfigPayload(cfg)).toEqual({ apiKey: 'sk-1' })
  })

  it('thinking 为非法值时不携带', () => {
    const cfg: LLMConfig = { apiKey: '', model: '', baseUrl: '', thinking: 'maybe' }
    expect(buildLlmConfigPayload(cfg)).toBeNull()
  })

  it('缺字段配置不崩溃（localStorage 手改/旧数据防御）', () => {
    // config 对象缺少 baseUrl 字段（type 断言绕过 TS），应回退空串而非 undefined.trim() 崩溃
    const cfg = { model: 'openai/gpt-4o', apiKey: 'sk-1', thinking: 'enabled' } as unknown as LLMConfig
    expect(buildLlmConfigPayload(cfg)).toEqual({ model: 'openai/gpt-4o', apiKey: 'sk-1', thinking: 'enabled' })
  })

  it('仅 thinking 有效但其余为空时仍输出 thinking', () => {
    const cfg: LLMConfig = { apiKey: '', model: '', baseUrl: '', thinking: 'disabled' }
    expect(buildLlmConfigPayload(cfg)).toEqual({ thinking: 'disabled' })
  })

  it('contextLength 为正整数时携带', () => {
    const cfg: LLMConfig = {
      apiKey: 'sk-1',
      model: 'openai/gpt-4o',
      baseUrl: 'https://x/v1',
      thinking: 'enabled',
      contextLength: 200000,
    }
    expect(buildLlmConfigPayload(cfg)).toEqual({
      model: 'openai/gpt-4o',
      baseUrl: 'https://x/v1',
      apiKey: 'sk-1',
      thinking: 'enabled',
      contextLength: 200000,
    })
  })

  it('contextLength 未设置时不携带（跟随后端静态默认）', () => {
    const cfg: LLMConfig = { apiKey: 'sk-1', model: 'openai/gpt-4o', baseUrl: 'https://x/v1', thinking: 'enabled' }
    const payload = buildLlmConfigPayload(cfg)
    expect(payload?.contextLength).toBeUndefined()
  })

  it('携带 capability 时透传（供后端系统操作工具门禁）', () => {
    const capability = {
      non_stream: true,
      stream: true,
      tool_call: false,
      tool_followup: false,
      json_output: true,
    }
    const cfg: LLMConfig = { apiKey: 'sk-1', model: 'm', baseUrl: '', thinking: '', capability }
    expect(buildLlmConfigPayload(cfg)).toEqual({
      model: 'm',
      apiKey: 'sk-1',
      capability,
    })
  })
})

describe('isDeepSeekModel', () => {
  it('包含 deepseek（任意大小写）返回 true', () => {
    expect(isDeepSeekModel('deepseek/deepseek-chat')).toBe(true)
    expect(isDeepSeekModel('DeepSeek/Reasoner')).toBe(true)
    expect(isDeepSeekModel('DEEPSEEK')).toBe(true)
  })

  it('非 deepseek 模型返回 false', () => {
    expect(isDeepSeekModel('openai/gpt-4o')).toBe(false)
    expect(isDeepSeekModel('anthropic/claude-sonnet-4-20250514')).toBe(false)
    expect(isDeepSeekModel('')).toBe(false)
  })
})

describe('matchPreset - 预设匹配', () => {
  it('DeepSeek 预设精确匹配', () => {
    const preset = PROVIDER_PRESETS.find((p) => p.name === 'DeepSeek 官方')!
    expect(matchPreset({ model: preset.model, baseUrl: preset.baseUrl, thinking: preset.thinking })).toBe('DeepSeek 官方')
  })

  it('OpenAI 预设精确匹配（baseUrl 为空、thinking 为空）', () => {
    const preset = PROVIDER_PRESETS.find((p) => p.name === 'OpenAI')!
    expect(matchPreset({ model: preset.model, baseUrl: preset.baseUrl, thinking: preset.thinking })).toBe('OpenAI')
  })

  it('手动修改任一字段后返回"自定义"', () => {
    const preset = PROVIDER_PRESETS.find((p) => p.name === 'DeepSeek 官方')!
    expect(matchPreset({ model: 'deepseek/deepseek-reasoner', baseUrl: preset.baseUrl, thinking: preset.thinking })).toBe(CUSTOM_PRESET_NAME)
    expect(matchPreset({ model: preset.model, baseUrl: 'https://other/v1', thinking: preset.thinking })).toBe(CUSTOM_PRESET_NAME)
    expect(matchPreset({ model: preset.model, baseUrl: preset.baseUrl, thinking: 'disabled' })).toBe(CUSTOM_PRESET_NAME)
  })

  it('全空值返回"自定义"', () => {
    expect(matchPreset({ model: '', baseUrl: '', thinking: '' })).toBe(CUSTOM_PRESET_NAME)
  })
})

describe('buildModelWithPrefix - litellm 前缀推导', () => {
  it('原始模型已含 / 直接返回', () => {
    expect(buildModelWithPrefix('deepseek/deepseek-chat', 'https://api.deepseek.com/v1')).toBe('deepseek/deepseek-chat')
  })

  it('从 baseUrl 域名主体推导前缀（api.deepseek.com → deepseek）', () => {
    expect(buildModelWithPrefix('deepseek-chat', 'https://api.deepseek.com/v1')).toBe('deepseek/deepseek-chat')
  })

  it('域名含多级时取主体段（过滤 api/com 等）', () => {
    expect(buildModelWithPrefix('gpt-4o', 'https://api.openai.com/v1')).toBe('openai/gpt-4o')
  })

  it('域名推导前缀不在白名单时回退 openai（kimi）', () => {
    expect(buildModelWithPrefix('kimi-k2-0905-preview', 'https://api.kimi.ai/v1')).toBe('openai/kimi-k2-0905-preview')
  })

  it('域名推导前缀在白名单时保持推导值（anthropic）', () => {
    expect(buildModelWithPrefix('claude-sonnet-4', 'https://api.anthropic.com/v1')).toBe('anthropic/claude-sonnet-4')
  })

  it('未知主机兜底段也走白名单回退', () => {
    expect(buildModelWithPrefix('some-model', 'https://foo.bar/v1')).toBe('openai/some-model')
  })

  it('baseUrl 为空时返回原始模型名', () => {
    expect(buildModelWithPrefix('llama3', '')).toBe('llama3')
  })

  it('baseUrl 非法（缺少协议）时返回原始模型名', () => {
    expect(buildModelWithPrefix('llama3', 'not-a-url')).toBe('llama3')
  })

  it('空模型名返回空字符串', () => {
    expect(buildModelWithPrefix('  ', 'https://api.deepseek.com/v1')).toBe('')
  })

  it('裁剪模型名前后空白', () => {
    expect(buildModelWithPrefix('  deepseek-chat  ', 'https://api.deepseek.com/v1')).toBe('deepseek/deepseek-chat')
  })
})

// ── 多配置管理（profiles）测试（add-custom-llm-api Task 10.6）──
// 覆盖：profile 增删、切换激活、旧 fa_llm_config 迁移、无 profile 回退默认配置。

describe('loadProfiles - profiles 读取与迁移', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('无 fa_llm_profiles 时返回空 store', () => {
    const store = loadProfiles()
    expect(store.profiles).toEqual([])
    expect(store.activeId).toBe('')
  })

  it('读取已存在的 fa_llm_profiles', () => {
    const profile: LLMProfile = { id: '1', name: 'DeepSeek 办公', config: { apiKey: 'sk-1', model: 'deepseek/deepseek-chat', baseUrl: 'https://api.deepseek.com/v1', thinking: 'enabled' } }
    const store: ProfileStore = { profiles: [profile], activeId: '1' }
    localStorage.setItem(FA_LLM_PROFILES_KEY, JSON.stringify(store))
    const loaded = loadProfiles()
    expect(loaded.profiles).toHaveLength(1)
    expect(loaded.profiles[0].name).toBe('DeepSeek 办公')
    expect(loaded.activeId).toBe('1')
  })

  it('fa_llm_profiles 为损坏 JSON 时返回空 store', () => {
    localStorage.setItem(FA_LLM_PROFILES_KEY, '{not-json')
    const store = loadProfiles()
    expect(store.profiles).toEqual([])
    expect(store.activeId).toBe('')
  })

  it('fa_llm_profiles 不存在但旧 fa_llm_config 存在时自动迁移', () => {
    const cfg: LLMConfig = { apiKey: 'sk-1', model: 'openai/gpt-4o', baseUrl: '', thinking: '' }
    localStorage.setItem(FA_LLM_CONFIG_KEY, JSON.stringify(cfg))
    const store = loadProfiles()
    expect(store.profiles).toHaveLength(1)
    expect(store.profiles[0].name).toBe('旧配置')
    expect(store.profiles[0].config).toEqual({ ...cfg, apiForm: 'chat_completion' })
    expect(store.activeId).toBe(store.profiles[0].id)
    // 迁移后清除旧 key
    expect(localStorage.getItem(FA_LLM_CONFIG_KEY)).toBeNull()
  })

  it('fa_llm_profiles 不存在但旧 fa_api_key 存在时也迁移', () => {
    localStorage.setItem('fa_api_key', 'sk-legacy')
    const store = loadProfiles()
    expect(store.profiles).toHaveLength(1)
    expect(store.profiles[0].config.apiKey).toBe('sk-legacy')
    expect(store.activeId).toBe(store.profiles[0].id)
    expect(localStorage.getItem('fa_api_key')).toBeNull()
  })

  it('fa_llm_profiles 存在时不触发迁移（即使旧 key 也在）', () => {
    const profile: LLMProfile = { id: '1', name: '测试', config: emptyLlmConfig() }
    localStorage.setItem(FA_LLM_PROFILES_KEY, JSON.stringify({ profiles: [profile], activeId: '1' }))
    localStorage.setItem(FA_LLM_CONFIG_KEY, JSON.stringify({ apiKey: 'sk-old', model: '', baseUrl: '', thinking: '' }))
    const store = loadProfiles()
    expect(store.profiles).toHaveLength(1)
    expect(store.profiles[0].name).toBe('测试')
    // 旧 key 未被清除（profiles 优先，不触发迁移）
    expect(localStorage.getItem(FA_LLM_CONFIG_KEY)).toBeTruthy()
  })
})

describe('saveProfiles - profiles 写入', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('序列化为 JSON 存入 fa_llm_profiles', () => {
    const store: ProfileStore = {
      profiles: [{ id: '1', name: 'P1', config: emptyLlmConfig() }],
      activeId: '1',
    }
    saveProfiles(store)
    expect(JSON.parse(localStorage.getItem(FA_LLM_PROFILES_KEY)!)).toEqual(store)
  })
})

describe('addProfile - 新增配置', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('添加第一个 profile 并设为激活', () => {
    const cfg: LLMConfig = { apiKey: 'sk-1', model: 'deepseek/deepseek-chat', baseUrl: 'https://api.deepseek.com/v1', thinking: 'enabled' }
    const store = addProfile(loadProfiles(), 'DeepSeek 办公', cfg)
    expect(store.profiles).toHaveLength(1)
    expect(store.profiles[0].name).toBe('DeepSeek 办公')
    expect(store.profiles[0].config).toEqual(cfg)
    expect(store.activeId).toBe(store.profiles[0].id)
  })

  it('添加第二个 profile 并自动设为激活', () => {
    const cfg1: LLMConfig = { apiKey: 'sk-1', model: 'm1', baseUrl: '', thinking: '' }
    const cfg2: LLMConfig = { apiKey: 'sk-2', model: 'm2', baseUrl: '', thinking: '' }
    let store = addProfile(loadProfiles(), 'P1', cfg1)
    store = addProfile(store, 'P2', cfg2)
    expect(store.profiles).toHaveLength(2)
    // 新增的 profile 成为激活项
    expect(store.activeId).toBe(store.profiles[1].id)
  })

  it('名称为空时不添加（返回原 store）', () => {
    const store = addProfile(loadProfiles(), '', emptyLlmConfig())
    expect(store.profiles).toEqual([])
  })

  it('id 唯一（两个 profile id 不同）', () => {
    let store = addProfile(loadProfiles(), 'P1', emptyLlmConfig())
    store = addProfile(store, 'P2', emptyLlmConfig())
    expect(store.profiles[0].id).not.toBe(store.profiles[1].id)
  })
})

describe('deleteProfile - 删除配置', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('删除非激活 profile 不影响激活项', () => {
    let store = addProfile(loadProfiles(), 'P1', { apiKey: 'sk-1', model: 'm1', baseUrl: '', thinking: '' })
    store = addProfile(store, 'P2', { apiKey: 'sk-2', model: 'm2', baseUrl: '', thinking: '' })
    const activeIdBefore = store.activeId
    // 删除 P1（非激活）
    store = deleteProfile(store, store.profiles[0].id)
    expect(store.profiles).toHaveLength(1)
    expect(store.activeId).toBe(activeIdBefore)
  })

  it('删除激活 profile 时自动激活剩余第一个', () => {
    let store = addProfile(loadProfiles(), 'P1', { apiKey: 'sk-1', model: 'm1', baseUrl: '', thinking: '' })
    store = addProfile(store, 'P2', { apiKey: 'sk-2', model: 'm2', baseUrl: '', thinking: '' })
    // 当前激活的是 P2，删除 P2
    store = deleteProfile(store, store.activeId)
    expect(store.profiles).toHaveLength(1)
    expect(store.activeId).toBe(store.profiles[0].id)
  })

  it('删除最后一个 profile 后 activeId 为空', () => {
    let store = addProfile(loadProfiles(), 'P1', { apiKey: 'sk-1', model: 'm1', baseUrl: '', thinking: '' })
    store = deleteProfile(store, store.activeId)
    expect(store.profiles).toEqual([])
    expect(store.activeId).toBe('')
  })
})

describe('activateProfile - 切换激活', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('切换激活到指定 profile', () => {
    let store = addProfile(loadProfiles(), 'P1', { apiKey: 'sk-1', model: 'm1', baseUrl: '', thinking: '' })
    store = addProfile(store, 'P2', { apiKey: 'sk-2', model: 'm2', baseUrl: '', thinking: '' })
    // 当前激活 P2，切换到 P1
    store = activateProfile(store, store.profiles[0].id)
    expect(store.activeId).toBe(store.profiles[0].id)
  })

  it('激活不存在的 id 时保持不变', () => {
    let store = addProfile(loadProfiles(), 'P1', { apiKey: 'sk-1', model: 'm1', baseUrl: '', thinking: '' })
    store = activateProfile(store, 'nonexistent')
    expect(store.activeId).toBe(store.profiles[0].id)
  })
})

describe('getActiveConfig - 获取激活配置', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('无 profile 时返回空配置', () => {
    const cfg = getActiveConfig(loadProfiles())
    expect(cfg).toEqual(emptyLlmConfig())
  })

  it('返回激活 profile 的 config', () => {
    const customCfg: LLMConfig = { apiKey: 'sk-x', model: 'openai/gpt-4o', baseUrl: 'https://x/v1', thinking: '' }
    let store = addProfile(loadProfiles(), 'P1', customCfg)
    store = addProfile(store, 'P2', emptyLlmConfig())
    // 切换回 P1
    store = activateProfile(store, store.profiles[0].id)
    expect(getActiveConfig(store)).toEqual(customCfg)
  })

  it('activeId 无效时回退到第一个 profile', () => {
    const store: ProfileStore = {
      profiles: [{ id: '1', name: 'P1', config: { apiKey: 'sk-1', model: 'm1', baseUrl: '', thinking: '' } }],
      activeId: 'invalid',
    }
    expect(getActiveConfig(store).apiKey).toBe('sk-1')
  })
})

describe('getActiveProfileName - 获取激活配置名', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('无 profile 时返回"未配置"', () => {
    expect(getActiveProfileName(loadProfiles())).toBe('未配置')
  })

  it('返回激活 profile 的名称', () => {
    let store = addProfile(loadProfiles(), 'DeepSeek 办公', emptyLlmConfig())
    expect(getActiveProfileName(store)).toBe('DeepSeek 办公')
  })
})

describe('apiForm - API 形式（add-llm-api-form）', () => {
  it('payload 显式携带合法 apiForm', () => {
    const out = buildLlmConfigPayload({ model: 'x/y', baseUrl: 'https://x', apiKey: 'k', thinking: '', apiForm: 'messages' })
    expect(out?.apiForm).toBe('messages')
  })
  it('apiForm 为空/非法时不携带（后端自动路由）', () => {
    const outEmpty = buildLlmConfigPayload({ model: 'x/y', baseUrl: 'https://x', apiKey: 'k', thinking: '' })
    expect(outEmpty?.apiForm).toBeUndefined()
    const outInvalid = buildLlmConfigPayload({ model: 'x/y', baseUrl: 'https://x', apiKey: 'k', thinking: '', apiForm: 'bogus' } as LLMConfig)
    expect(outInvalid?.apiForm).toBeUndefined()
  })

  it('loadLlmConfig 读取合法 apiForm', () => {
    const stored: LLMConfig = { apiKey: 'sk-1', model: 'gpt-4o', baseUrl: 'https://x/v1', thinking: '', apiForm: 'responses' }
    localStorage.clear()
    localStorage.setItem(FA_LLM_CONFIG_KEY, JSON.stringify(stored))
    expect(loadLlmConfig().apiForm).toBe('responses')
  })
  it('loadLlmConfig 丢弃非法/缺失 apiForm 并回退默认 OpenAI Chat Completion', () => {
    localStorage.clear()
    localStorage.setItem(FA_LLM_CONFIG_KEY, JSON.stringify({ apiKey: 'k', model: 'm', baseUrl: 'b', thinking: '', apiForm: 'bogus' }))
    expect(loadLlmConfig().apiForm).toBe('chat_completion')
  })

  it('Anthropic 预设携带 messages，OpenAI 预设携带 chat_completion', () => {
    const anthropic = PROVIDER_PRESETS.find((p) => p.name === 'Anthropic')!
    const openai = PROVIDER_PRESETS.find((p) => p.name === 'OpenAI')!
    expect(anthropic.apiForm).toBe('messages')
    expect(openai.apiForm).toBe('chat_completion')
  })
})
