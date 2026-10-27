import { useScrollProgress } from '../lib/motion'

/** A hairline at the very top of the viewport showing how far the page has been read. */
export function ScrollRail() {
  const progress = useScrollProgress()
  if (progress <= 0.002) return null
  return <div className="scroll-rail" style={{ ['--progress' as string]: progress }} aria-hidden="true" />
}
