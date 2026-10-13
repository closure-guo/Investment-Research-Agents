// 设计令牌——语义层（unify-ui-design-system T2.1，spec: frontend delta）
//
// 为什么需要语义令牌而不是裸色值：
// - 涨跌色遵循 A 股惯例（红涨绿跌），与编码 agent 训练语料里的国际惯例相反，
//   写死语义名 rise/fall/flat 可从机制上防止页面直接写 text-red-*/text-green-*；
// - 状态徽章五态与 AI 生成内容标识是全站统一视觉语言，页面不得自绘；
// - 浅色/深色双主题变量在 src/index.css 的 :root 与 .dark 内声明，本文件只导出
//   指向变量的引用（var(--x)），保证主题切换时语义自动跟随。
//
// 本文件为纯常量模块（无 React 依赖），可被测试直接快照。

/** 涨跌语义键：rise 红（涨）/ fall 绿（跌）/ flat 灰（持平/无读数） */
export type SignKey = 'rise' | 'fall' | 'flat'

/** 状态徽章五态（spec 钉死的唯一枚举，禁止扩展自绘状态） */
export type StatusKey = 'active' | 'degraded' | 'retired' | 'running' | 'failed'

export const RISE_COLOR = 'var(--rise)'
export const FALL_COLOR = 'var(--fall)'
export const FLAT_COLOR = 'var(--flat)'

export const SIGN_COLORS: Record<SignKey, string> = {
  rise: RISE_COLOR,
  fall: FALL_COLOR,
  flat: FLAT_COLOR,
}

/** 五态徽章顺序（同时作为测试与文档的枚举清单） */
export const STATUS_KEYS: readonly StatusKey[] = ['active', 'degraded', 'retired', 'running', 'failed']

export interface BadgeToken {
  /** 底色变量 */
  bg: string
  /** 前景（文字）变量 */
  fg: string
  /** 描边变量（实心态与底色同值） */
  border: string
  /** 默认文案（可被调用方 label 覆盖） */
  label: string
  /** 呼吸动画标记：仅 running 态 */
  animation?: 'breath'
}

/**
 * 五态徽章令牌：active 实心绿底 / degraded 琥珀描边 / retired 灰底 /
 * running 蓝底（+呼吸动画）/ failed 红底。深浅主题语义一致，取值见 index.css。
 */
export const BADGE_TOKENS: Record<StatusKey, BadgeToken> = {
  active: {
    bg: 'var(--badge-active-bg)',
    fg: 'var(--badge-active-fg)',
    border: 'var(--badge-active-border)',
    label: '正常',
  },
  degraded: {
    bg: 'var(--badge-degraded-bg)',
    fg: 'var(--badge-degraded-fg)',
    border: 'var(--badge-degraded-border)',
    label: '降级',
  },
  retired: {
    bg: 'var(--badge-retired-bg)',
    fg: 'var(--badge-retired-fg)',
    border: 'var(--badge-retired-border)',
    label: '已停用',
  },
  running: {
    bg: 'var(--badge-running-bg)',
    fg: 'var(--badge-running-fg)',
    border: 'var(--badge-running-border)',
    label: '运行中',
    animation: 'breath',
  },
  failed: {
    bg: 'var(--badge-failed-bg)',
    fg: 'var(--badge-failed-fg)',
    border: 'var(--badge-failed-border)',
    label: '失败',
  },
}

/** AI 生成内容标识色（紫系）：角标与提示引用同一族变量 */
export const AI_ACCENT = {
  accent: 'var(--ai-accent)',
  bg: 'var(--ai-accent-bg)',
  fg: 'var(--ai-accent-fg)',
  border: 'var(--ai-accent-border)',
} as const

/**
 * 图表规范常量（ECharts）。
 *
 * 本 change 只交付约定本身，不重构既有图表（Charts.tsx 保持原样）；
 * 后续新增图表须按此组装：主色实线 / 基准灰虚线 / 损耗半透明填充 / 事件标记图标集。
 */
export const CHART_CONVENTIONS = {
  /** 主曲线：主色实线 */
  mainLine: { color: 'var(--chart-sky)', lineStyle: 'solid', lineWidth: 2 },
  /** 基准线（如基准收益率/入场价参照）：灰虚线 */
  baseline: { color: 'var(--border)', lineStyle: 'dashed', lineWidth: 1 },
  /** 损耗/回撤区域：半透明填充 */
  lossFill: { color: 'var(--chart-coral)', opacity: 0.2 },
  /** 事件标记：图标集 + 统一标记色 */
  eventMarkers: {
    color: 'var(--chart-amber)',
    icons: {
      earnings: 'fa-file-invoice-dollar',
      dividend: 'fa-coins',
      suspension: 'fa-circle-pause',
      risk: 'fa-triangle-exclamation',
    },
  },
} as const
