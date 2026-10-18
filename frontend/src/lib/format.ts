import type { Metric, Severity } from '../types/api'

export const METRIC_META: Record<Metric, { label: string; unit: string; short: string; color: string; aggregation: 'mean' | 'sum' }> = {
  temperature_c: { label: 'Mean temperature', unit: '°C', short: 'Temp', color: '#c0562f', aggregation: 'mean' },
  temperature_min_c: { label: 'Minimum temperature', unit: '°C', short: 'Min', color: '#3b9af6', aggregation: 'mean' },
  temperature_max_c: { label: 'Maximum temperature', unit: '°C', short: 'Max', color: '#e98513', aggregation: 'mean' },
  precipitation_mm: { label: 'Precipitation', unit: 'mm', short: 'Rain', color: '#257ceb', aggregation: 'sum' },
  relative_humidity_pct: { label: 'Relative humidity', unit: '%', short: 'RH', color: '#559c66', aggregation: 'mean' },
  wind_speed_ms: { label: 'Wind speed', unit: 'm/s', short: 'Wind', color: '#4d8f62', aggregation: 'mean' },
  wind_direction_deg: { label: 'Wind direction', unit: '°', short: 'Dir', color: '#94744e', aggregation: 'mean' },
  surface_pressure_hpa: { label: 'Surface pressure', unit: 'hPa', short: 'Pressure', color: '#1e51af', aggregation: 'mean' },
  cloud_coverage_pct: { label: 'Cloud coverage', unit: '%', short: 'Cloud', color: '#86978b', aggregation: 'mean' },
  solar_radiation_kwh_m2: { label: 'Solar radiation', unit: 'kWh/m²', short: 'Solar', color: '#e9b23c', aggregation: 'mean' },
}

export const ALL_METRICS = Object.keys(METRIC_META) as Metric[]

export function metricUnit(metric?: string | null): string {
  if (!metric) return ''
  return METRIC_META[metric as Metric]?.unit ?? ''
}

export function metricLabel(metric?: string | null): string {
  if (!metric) return 'Metric'
  return METRIC_META[metric as Metric]?.label ?? metric.replaceAll('_', ' ')
}

export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  const absolute = Math.abs(value)
  const fractionDigits = absolute >= 1000 ? 0 : absolute < 1 && absolute !== 0 ? Math.max(digits, 2) : digits
  return value.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: fractionDigits })
}

export function formatMetric(value: number | null | undefined, metric: Metric, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${formatNumber(value, digits)}${METRIC_META[metric]?.unit ? ` ${METRIC_META[metric].unit}` : ''}`
}

export function formatCompact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

export function formatBytes(value: number | null | undefined): string {
  if (!value && value !== 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let index = 0
  let size = value
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size.toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? null : parsed
}

export function formatDate(value: string | Date | null | undefined, withTime = false): string {
  const date = typeof value === 'string' ? parseDate(value) : value ?? null
  if (!date) return '—'
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
  }).format(date)
}

export function formatDateTime(value: string | Date | null | undefined): string {
  return formatDate(value, true)
}

export function formatTime(value: string | Date | null | undefined): string {
  const date = typeof value === 'string' ? parseDate(value) : value ?? null
  if (!date) return '—'
  return new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(date)
}

export function relativeTime(value: string | Date | null | undefined): string {
  const date = typeof value === 'string' ? parseDate(value) : value ?? null
  if (!date) return 'never'
  const seconds = Math.round((date.getTime() - Date.now()) / 1000)
  const absolute = Math.abs(seconds)
  const units: [Intl.RelativeTimeFormatUnit, number][] = [
    ['year', 31_536_000],
    ['month', 2_592_000],
    ['day', 86_400],
    ['hour', 3_600],
    ['minute', 60],
    ['second', 1],
  ]
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  for (const [unit, step] of units) {
    if (absolute >= step) return formatter.format(Math.round(seconds / step), unit)
  }
  return 'just now'
}

export function toIsoDate(value: Date | string | null | undefined): string {
  const date = typeof value === 'string' ? parseDate(value) : value ?? null
  if (!date) return ''
  return date.toISOString().slice(0, 10)
}

export function daysAgo(days: number): string {
  const date = new Date()
  date.setUTCDate(date.getUTCDate() - days)
  return toIsoDate(date)
}

export function severityTone(severity?: Severity | string | null): 'neutral' | 'success' | 'info' | 'warning' | 'danger' {
  switch (severity) {
    case 'critical':
    case 'high':
      return 'danger'
    case 'medium':
      return 'warning'
    case 'low':
      return 'info'
    case 'info':
      return 'neutral'
    default:
      return 'neutral'
  }
}

export function humanise(value?: string | null): string {
  if (!value) return ''
  return value
    .replaceAll('_', ' ')
    .split(' ')
    .map((word, index) => (index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word))
    .join(' ')
}

export function titleCaseWords(value: string): string {
  return value
    .split(/[\s-]+/)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ')
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${formatNumber(value * 100, digits)}%`
}
