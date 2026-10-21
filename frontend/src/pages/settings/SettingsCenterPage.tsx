// SettingsCenterPage 设置中心页（add-agent-settings-center Task 7）
// 左侧垂直导航（八分区）+ 右侧内容区；initialModule 定位默认激活分区。
// 分区组件：LLM 配置（复用弹窗抽取的 LlmConfigPane）/ 缓存管理 / 会话管理 / 运行信息 / 数据监控 /
// 因子研发配置（add-factor-research-settings）/ 战绩展示偏好 /
// 评估运维（add-eval-ops-console Task 6：日批状态与补跑 / cohort 控制 / 回测与探针 / 健康检查 / 报告注册表 / 预登记与口径）。
import { useState, type CSSProperties } from 'react'
import { LlmConfigPane } from './panes/LlmConfigPane'
import { CachePane } from './panes/CachePane'
import { SessionsPane } from './panes/SessionsPane'
import { RunInfoPane } from './panes/RunInfoPane'
import { DataMonitorPane } from './panes/DataMonitorPane'
import { TrackPrefsPane } from './panes/TrackPrefsPane'
import { EvalOpsPane } from './panes/EvalOpsPane'
import { FactorResearchPane } from './panes/FactorResearchPane'
import type { LLMConfig, CapabilityMatrix, ProfileStore } from '../../llmConfig'

export type ModuleId =
  | 'llm'
  | 'cache'
  | 'sessions'
  | 'run'
  | 'monitor'
  | 'factor-research'
  | 'track'
  | 'eval-ops'

const MODULES: { id: ModuleId; label: string }[] = [
  { id: 'llm', label: 'LLM 配置' },
  { id: 'cache', label: '缓存管理' },
  { id: 'sessions', label: '会话管理' },
  { id: 'run', label: '运行信息' },
  { id: 'monitor', label: '数据监控' },
  { id: 'factor-research', label: '因子研发配置' },
  { id: 'track', label: '战绩展示偏好' },
  { id: 'eval-ops', label: '评估运维' },
]

const navButtonStyle: CSSProperties = {
  display: 'block', width: '100%', textAlign: 'left', padding: '8px 10px', marginBottom: 4,
  borderRadius: 6, cursor: 'pointer', border: 'none', fontSize: 13,
}

export function SettingsCenterPage(props: {
  config: LLMConfig
  backendDefaults: { model: string; baseUrl: string; thinking: string }
  profileStore: ProfileStore
  capability: CapabilityMatrix | null
  onProbeCapability: (cap: CapabilityMatrix | null) => void
  onSave: (cfg: LLMConfig) => void
  onSaveAs: (cfg: LLMConfig, name: string) => void
  onSwitchProfile: (id: string) => void
  onDeleteProfile: (id: string) => void
  onBack: () => void
  // 清空全部会话成功后的回调（独立于 onBack：清空需回到空态首页并重置会话状态，
  // 而非仅返回上一页）。由 App 注入真正的清空处理器；可选，缺省时仅由 SessionsPane
  // 内部刷新自身计数、不触发导航。
  onCleared?: () => void
  /** 因子研发配置分区 → 监控页跳转（delta add-factor-research-settings） */
  onOpenFactorResearch?: () => void
  initialModule?: ModuleId
}) {
  const [active, setActive] = useState<ModuleId>(props.initialModule ?? 'llm')
  return (
    <div data-testid="settings-center" style={{ display: 'flex', minHeight: '100vh' }}>
      <aside
        style={{
          width: 180, background: 'var(--bg-overlay-l1)',
          borderRight: '1px solid var(--border-neutral-l1)', padding: 12, flexShrink: 0,
        }}
      >
        <div style={{ fontWeight: 700, marginBottom: 12 }}>⚙ 设置</div>
        {MODULES.map((m) => (
          <button
            key={m.id}
            data-testid={`settings-nav-${m.id}`}
            onClick={() => setActive(m.id)}
            style={{
              ...navButtonStyle,
              background: active === m.id ? 'var(--bg-brand)' : 'transparent',
              color: active === m.id ? 'var(--text-onbrand)' : 'var(--text-default)',
            }}
          >
            {m.label}
          </button>
        ))}
        <button
          data-testid="settings-back"
          onClick={props.onBack}
          style={{
            ...navButtonStyle, marginTop: 12,
            background: 'transparent', color: 'var(--text-secondary)',
          }}
        >
          ← 返回
        </button>
      </aside>
      <main style={{ flex: 1, padding: 20, maxWidth: 720 }}>
        {active === 'llm' && (
          <LlmConfigPane
            config={props.config}
            backendDefaults={props.backendDefaults}
            profileStore={props.profileStore}
            capability={props.capability}
            onProbeCapability={props.onProbeCapability}
            onSave={props.onSave}
            onSaveAs={props.onSaveAs}
            onSwitchProfile={props.onSwitchProfile}
            onDeleteProfile={props.onDeleteProfile}
          />
        )}
        {active === 'cache' && <CachePane />}
        {active === 'sessions' && <SessionsPane onCleared={props.onCleared} />}
        {active === 'run' && <RunInfoPane />}
        {active === 'monitor' && <DataMonitorPane />}
        {active === 'factor-research' && (
          <FactorResearchPane onOpenMonitor={props.onOpenFactorResearch} />
        )}
        {active === 'track' && <TrackPrefsPane />}
        {active === 'eval-ops' && <EvalOpsPane />}
      </main>
    </div>
  )
}
