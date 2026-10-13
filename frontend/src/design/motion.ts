// 动效规范（unify-ui-design-system T5.1，spec: frontend delta）
//
// 参数写死（contract）：
// - 表格行入场：fade-in + translateY(8px)→0，stagger 30ms/行（封顶 600ms，避免长列表尾部等待）；
// - Tab 切换内容区：150ms 淡入；
// - 徽章状态变化：200ms ease-out 颜色过渡（.status-badge 在 index.css 内实现）。
// - 全部动效在 prefers-reduced-motion 下降级为无动画：CSS 媒体查询（index.css）+
//   JS 侧 useReducedMotion 门控（本文件）+ Tailwind motion-reduce:animate-none 三重保障。
import { useReducedMotion, type Variants } from 'framer-motion'

/** 行入场初始位移（px） */
export const ROW_ENTRANCE_Y = 8
/** 行入场时长（ms，framer-motion 侧换算为秒） */
export const ROW_ENTRANCE_DURATION_MS = 200
/** 行入场 stagger 间隔（ms） */
export const STAGGER_MS = 30
/** stagger 延迟封顶（ms）：只对首屏前 ~20 行有逐行节奏意义 */
export const MAX_STAGGER_DELAY_MS = 600
/** Tab 内容区淡入时长（ms，index.css .animate-tab-fade 同步） */
export const TAB_FADE_MS = 150
/** 徽章变色过渡时长（ms，index.css .status-badge 同步） */
export const BADGE_TRANSITION_MS = 200

/** Tab 内容区淡入类（替换 key 触发重挂载即可重播；reduced-motion 下无动画） */
export const TAB_FADE_CLASS = 'animate-tab-fade motion-reduce:animate-none'

/**
 * 行入场变体：hidden（opacity 0 + y 8px）→ show（opacity 1 + y 0）。
 * show 为函数变体，延迟按 custom={index} 计算：index * 30ms，封顶 600ms。
 */
export function rowEntranceVariants(): Variants {
  return {
    hidden: { opacity: 0, y: ROW_ENTRANCE_Y },
    show: (index: number = 0) => ({
      opacity: 1,
      y: 0,
      transition: {
        delay: Math.min(index * STAGGER_MS, MAX_STAGGER_DELAY_MS) / 1000,
        duration: ROW_ENTRANCE_DURATION_MS / 1000,
        ease: 'easeOut',
      },
    }),
  }
}

export interface RowEntrance {
  variants: Variants | undefined
  initial: false | 'hidden'
  animate: 'show' | undefined
  custom: number
}

/**
 * 行入场动效（表格行/列表项通用）。
 *
 * reduced-motion 时不返回任何动画配置（initial=false 且无 variants/animate），
 * 内容直接呈现——降级不等于隐藏。
 *
 * 用法：`const entrance = useRowEntrance(index); <motion.tr {...entrance}>`
 */
export function useRowEntrance(index = 0): RowEntrance {
  const reduced = useReducedMotion()
  if (reduced) {
    return { variants: undefined, initial: false, animate: undefined, custom: index }
  }
  return { variants: rowEntranceVariants(), initial: 'hidden', animate: 'show', custom: index }
}
