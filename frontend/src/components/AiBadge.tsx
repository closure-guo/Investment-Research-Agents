import { cn } from '../lib/utils'
import { AI_ACCENT } from '../design/tokens'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './ui/tooltip'

/** AI 生成内容角标的唯一提示文案（spec 钉死，禁止改写） */
export const AI_BADGE_HINT = '由模型生成，仅供参考'

/**
 * AI 生成内容标识角标（unify-ui-design-system T4.2，spec: frontend delta）。
 *
 * 凡 LLM 生成的内容（L3 粗筛理由、语义记忆条款、分析结论等）携带本角标；
 * 确定性数据（数字、状态、时间）不携带。
 *
 * - 紫系小角标（AI_ACCENT 令牌，双主题跟随）；
 * - hover / 键盘聚焦展示提示「由模型生成，仅供参考」（shadcn Tooltip 原语）；
 * - 可访问：role="img" + aria-label 直接暴露提示文案，不依赖 hover。
 *
 * @param corner 角标模式：绝对定位到内容块右上角（调用方需提供 relative 容器）
 */
export function AiBadge({
  label = 'AI',
  corner = false,
  className,
}: {
  label?: React.ReactNode
  corner?: boolean
  className?: string
}) {
  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            data-testid="ai-badge"
            role="img"
            aria-label={AI_BADGE_HINT}
            tabIndex={0}
            className={cn(
              'inline-flex cursor-default select-none items-center gap-1 rounded-full border px-1.5 py-0.5 text-[10px] font-medium leading-none',
              corner && 'absolute -right-1 -top-1',
              className,
            )}
            style={{ backgroundColor: AI_ACCENT.bg, color: AI_ACCENT.fg, borderColor: AI_ACCENT.border }}
          >
            <i className="fas fa-wand-magic-sparkles text-[9px]" aria-hidden="true"></i>
            {label}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" data-testid="ai-badge-tooltip">
          {AI_BADGE_HINT}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
