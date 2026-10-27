import { useEffect, useRef, useState, type RefObject } from 'react'

const NUMERIC_ONLY = /^-?[\d,]*(\.\d+)?$/

interface Parsed {
  value: number
  decimals: number
  grouped: boolean
}

/** Only a string that is nothing but a number qualifies; "8 / 16" and dates pass through. */
function parseNumeric(text: string): Parsed | null {
  const trimmed = text.trim()
  if (!trimmed || !NUMERIC_ONLY.test(trimmed)) return null
  const normalised = trimmed.replace(/,/g, '')
  const value = Number(normalised)
  if (!Number.isFinite(value)) return null
  const dot = normalised.indexOf('.')
  return {
    value,
    decimals: dot === -1 ? 0 : normalised.length - dot - 1,
    grouped: trimmed.includes(','),
  }
}

function format(parsed: Parsed, shown: number): string {
  return parsed.grouped
    ? shown.toLocaleString('en-US', {
        minimumFractionDigits: parsed.decimals,
        maximumFractionDigits: parsed.decimals,
      })
    : shown.toFixed(parsed.decimals)
}

/**
 * Counts a figure up the first time it scrolls into view, so a dashboard assembles
 * itself instead of appearing fully formed. Attach the ref to the element that shows
 * `text`; anything that is not purely numeric is returned unchanged.
 */
export function useCountUp<T extends HTMLElement>(text: string): { ref: RefObject<T>; display: string } {
  const ref = useRef<T>(null)
  const parsed = parseNumeric(text)
  const [display, setDisplay] = useState(() => (parsed ? format(parsed, 0) : text))

  useEffect(() => {
    if (!parsed) {
      setDisplay(text)
      return
    }
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches || typeof IntersectionObserver === 'undefined') {
      setDisplay(format(parsed, parsed.value))
      return
    }
    const node = ref.current
    if (!node) return

    let frame = 0
    let running = false
    const observer = new IntersectionObserver(
      (entries) => {
        if (running || !entries.some((entry) => entry.isIntersecting)) return
        running = true
        observer.disconnect()
        const duration = 620
        const startedAt = performance.now()
        const tick = (now: number) => {
          const progress = Math.min(1, (now - startedAt) / duration)
          const eased = 1 - Math.pow(1 - progress, 3)
          setDisplay(format(parsed, parsed.value * eased))
          if (progress < 1) frame = requestAnimationFrame(tick)
        }
        frame = requestAnimationFrame(tick)
      },
      { threshold: 0.25 },
    )
    observer.observe(node)
    return () => {
      observer.disconnect()
      cancelAnimationFrame(frame)
    }
  }, [text])

  return { ref, display: parsed ? display : text }
}
