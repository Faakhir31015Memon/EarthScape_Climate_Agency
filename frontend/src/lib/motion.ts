import { useEffect, useRef, useState, type RefObject } from 'react'

/** The OS veto is respected everywhere: transitions, count-ups and the WebGL scene all read this. */
export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

let webglCache: boolean | null = null

/**
 * A single probe, cached. Software-rendered and blocked contexts both report false, and the
 * caller then shows the CSS rendering of the same figure instead of an empty box.
 */
export function hasWebGL(): boolean {
  if (webglCache !== null) return webglCache
  try {
    const canvas = document.createElement('canvas')
    webglCache = Boolean(canvas.getContext('webgl2') ?? canvas.getContext('webgl'))
  } catch {
    webglCache = false
  }
  return webglCache
}

interface InViewOptions {
  /** Fraction of the node that must be visible before it counts as arrived. */
  threshold?: number
  /** Keep firing on every entry after the first; panel reveals stay one-shot. */
  repeat?: boolean
  rootMargin?: string
}

/**
 * Reports when a node first enters the viewport. The node is expected to render its
 * settled state until this flips, so a browser without IntersectionObserver shows
 * everything at full opacity rather than nothing.
 */
export function useInView<T extends HTMLElement>(options: InViewOptions = {}): { ref: RefObject<T>; inView: boolean } {
  const { threshold = 0.18, repeat = false, rootMargin = '0px 0px -8% 0px' } = options
  const ref = useRef<T>(null)
  const [inView, setInView] = useState(false)

  useEffect(() => {
    const node = ref.current
    if (!node || typeof IntersectionObserver === 'undefined') {
      setInView(true)
      return
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (!entries.some((entry) => entry.isIntersecting)) return
        setInView(true)
        if (!repeat) observer.disconnect()
      },
      { threshold, rootMargin },
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [threshold, repeat, rootMargin])

  return { ref, inView }
}

/**
 * Paints the pointer position into --mx/--my on whatever card is under it, so the
 * surface catches light where the operator is actually looking. One document-level
 * listener, rAF-batched, and it never runs on a touch device.
 */
export function attachPointerSpotlight(): () => void {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}
  if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return () => {}

  const SELECTOR = '.card, .card-tight, .panel-surface'
  let pending: PointerEvent | null = null
  let frame = 0

  const flush = () => {
    frame = 0
    const event = pending
    pending = null
    if (!event) return
    const target = (event.target as Element | null)?.closest?.(SELECTOR) as HTMLElement | null
    if (!target) return
    const box = target.getBoundingClientRect()
    target.style.setProperty('--mx', `${event.clientX - box.left}px`)
    target.style.setProperty('--my', `${event.clientY - box.top}px`)
  }

  const onMove = (event: PointerEvent) => {
    pending = event
    if (!frame) frame = requestAnimationFrame(flush)
  }

  document.addEventListener('pointermove', onMove, { passive: true })
  return () => {
    document.removeEventListener('pointermove', onMove)
    if (frame) cancelAnimationFrame(frame)
  }
}

/**
 * Fades the page chrome in as the document scrolls, and reports the fraction travelled.
 * Used by the reading-progress rail at the top of the public pages.
 */
export function useScrollProgress(): number {
  const [progress, setProgress] = useState(0)

  useEffect(() => {
    if (typeof window === 'undefined') return
    let frame = 0
    const measure = () => {
      frame = 0
      const doc = document.documentElement
      const scrollable = doc.scrollHeight - doc.clientHeight
      setProgress(scrollable > 24 ? Math.min(1, Math.max(0, doc.scrollTop / scrollable)) : 0)
    }
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(measure)
    }
    measure()
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [])

  return progress
}
