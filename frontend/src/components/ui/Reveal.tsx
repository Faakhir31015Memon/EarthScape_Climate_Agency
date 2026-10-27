import { createElement, type CSSProperties, type ElementType, type ReactNode } from 'react'
import { cn } from '../../lib/cn'
import { useInView } from '../../lib/motion'

interface RevealProps {
  as?: ElementType
  /** Stagger within a group; the CSS keeps the node visible when motion is off. */
  delay?: number
  className?: string
  style?: CSSProperties
  children: ReactNode
}

/**
 * Fades and lifts a block into view the first time it is scrolled to. The hidden state
 * only exists on a document that opted into motion, so this can never strand content at
 * zero opacity if the observer or the stylesheet does not load.
 */
export function Reveal({ as = 'div', delay = 0, className, style, children }: RevealProps) {
  const { ref, inView } = useInView<HTMLElement>()
  return createElement(
    as,
    {
      ref,
      'data-reveal': '',
      className: cn(className, inView && 'is-in'),
      style: { ...style, ['--reveal-delay' as string]: `${delay}ms` },
    },
    children,
  )
}
