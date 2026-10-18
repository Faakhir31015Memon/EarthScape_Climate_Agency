import { useMemo } from 'react'
import type { Severity } from '../../types/api'
import { humanise } from '../../lib/format'

export function Sparkline({
  values,
  width = 120,
  height = 32,
  stroke = 'var(--brand)',
  ariaLabel,
}: {
  values: (number | null | undefined)[]
  width?: number
  height?: number
  stroke?: string
  ariaLabel?: string
}) {
  const path = useMemo(() => {
    const points = values.map((value, index) => ({ index, value: value === null || value === undefined ? null : Number(value) }))
    const defined = points.filter((point) => point.value !== null && Number.isFinite(point.value)) as { index: number; value: number }[]
    if (defined.length < 2) return ''
    const min = Math.min(...defined.map((point) => point.value))
    const max = Math.max(...defined.map((point) => point.value))
    const span = max - min || 1
    return defined
      .map((point, position) => {
        const x = (point.index / (values.length - 1 || 1)) * width
        const y = height - ((point.value - min) / span) * (height - 4) - 2
        return `${position === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`
      })
      .join(' ')
  }, [values, width, height])

  if (!path) {
    return <span className="text-xs subtle">insufficient data</span>
  }
  return (
    <svg width={width} height={height} role="img" aria-label={ariaLabel ?? 'trend'} viewBox={`0 0 ${width} ${height}`} className="overflow-visible">
      <path d={path} fill="none" stroke={stroke} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#c63a3a',
  high: '#e2743b',
  medium: '#e9b23c',
  low: '#3b9af6',
  info: 'var(--border-strong)',
}

export function SeverityBar({ counts, total }: { counts: Record<string, number>; total?: number }) {
  const entries = Object.entries(counts).filter(([, value]) => value > 0)
  const sum = total ?? entries.reduce((accumulator, [, value]) => accumulator + value, 0)
  if (!entries.length || sum === 0) {
    return <p className="text-xs subtle">No alerts in this breakdown.</p>
  }
  return (
    <div>
      <div className="flex h-2.5 w-full overflow-hidden rounded-full" style={{ backgroundColor: 'var(--surface-muted)' }}>
        {entries.map(([severity, value]) => (
          <div
            key={severity}
            title={`${humanise(severity)}: ${value}`}
            style={{ width: `${(value / sum) * 100}%`, backgroundColor: SEVERITY_COLORS[severity] ?? 'var(--border-strong)' }}
          />
        ))}
      </div>
      <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {entries.map(([severity, value]) => (
          <li key={severity} className="flex items-center gap-1.5 muted">
            <span className="h-2 w-2 rounded-full" style={{ backgroundColor: SEVERITY_COLORS[severity] ?? 'var(--border-strong)' }} aria-hidden="true" />
            {humanise(severity)} <span className="font-semibold">{value}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function ScoreRing({ score, label, size = 92 }: { score: number | null; label: string; size?: number }) {
  const radius = (size - 10) / 2
  const circumference = 2 * Math.PI * radius
  const clamped = score === null ? 0 : Math.max(0, Math.min(100, score))
  const color = clamped >= 85 ? '#27633a' : clamped >= 60 ? '#e98513' : clamped > 0 ? '#e2743b' : 'var(--border-strong)'
  return (
    <div className="flex items-center gap-3">
      <svg width={size} height={size} role="img" aria-label={`${label}: ${score === null ? 'unavailable' : `${clamped} of 100`}`}>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="var(--surface-muted)" strokeWidth={7} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={7}
          strokeLinecap="round"
          strokeDasharray={`${(clamped / 100) * circumference} ${circumference}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
        <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central" className="font-display" style={{ fontSize: 18, fill: 'var(--text)', fontWeight: 600 }}>
          {score === null ? '—' : Math.round(clamped)}
        </text>
      </svg>
      <div className="text-xs">
        <p className="font-semibold">{label}</p>
        <p className="muted">0–100, computed from observation completeness and quality flags.</p>
      </div>
    </div>
  )
}

export function severityColor(severity?: Severity | string | null): string {
  if (!severity) return 'var(--border-strong)'
  return SEVERITY_COLORS[severity] ?? 'var(--border-strong)'
}
