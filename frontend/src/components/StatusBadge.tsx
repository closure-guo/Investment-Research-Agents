import { useReducedMotion } from 'framer-motion'
import { cn } from '../lib/utils'
import { BADGE_TOKENS, type StatusKey } from '../design/tokens'

/**
 * 五态状态徽章（unify-ui-design-system T4.1，spec: frontend delta）。
 *
 * 状态枚举唯一来源为 design/tokens.ts 的 StatusKey（active/degraded/retired/running/failed）；
 * 任何页面展示状态必须复用本组件，禁止自绘状态样式。
 *
 * - 配色取自 BADGE_TOKENS（浅色/深色由 index.css 变量自动跟随主题）；
 * - running 态呼吸动画：index.css @keyframes badge-breath（.status-badge-running）；
 * - 动画/过渡三重降级：index.css prefers-reduced-motion 媒体查询 + 此处
 *   useReducedMotion 门控（不挂动画类）+ Tailwind motion-reduce:animate-none。
 * - 徽章变色过渡 200ms ease-out（.status-badge，与 BADGE_TRANSITION_MS 对齐）。
 */
export function StatusBadge({
  status,
  label,
  className,
}: {
  status: StatusKey
  /** 覆盖默认文案（如步骤级状态用步骤名）；省略则用五态默认文案 */
  label?: React.ReactNode
  className?: string
}) {
  const reduced = useReducedMotion()
  const token = BADGE_TOKENS[status]
  const animate = token.animation === 'breath' && !reduced

  return (
    <span
      data-testid="status-badge"
      data-status={status}
      className={cn(
        'status-badge inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium leading-none whitespace-nowrap motion-reduce:animate-none',
        animate && 'status-badge-running',
        className,
      )}
      style={{ backgroundColor: token.bg, color: token.fg, borderColor: token.border }}
    >
      {label ?? token.label}
    </span>
  )
}
