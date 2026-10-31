import { Fragment, useMemo } from 'react'
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  Pie,
  PieChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'
import type { FileCorrelationCell, FileForecastColumn, FileScatterPair, FileSeries, FileHeatmap } from '../../types/api'
import { formatNumber, humanise } from '../../lib/format'
import { AXIS_LINE, AXIS_TICK, ChartFooter, ChartLegend, ChartTooltipContent, DRAW_MS, GRID_STROKE, colorAt, useDrawIn } from './chartKit'

/**
 * Charts for an uploaded file. Nothing here knows about climate metrics: the backend
 * classifies the columns, and these views plot whatever numbers it found.
 */

const EMPTY = (
  <div className="flex items-center justify-center rounded-lg border border-dashed py-10 text-sm muted" style={{ borderColor: 'var(--border-strong)' }}>
    Nothing to plot in this selection.
  </div>
)

export type SeriesField = 'value' | 'sum' | 'min' | 'max'
export type TrendVariant = 'line' | 'area' | 'stacked' | 'bar' | 'range'
export type CategoryVariant = 'bar' | 'column' | 'pie' | 'donut' | 'radar'

export function FileTrendChart({
  series,
  field = 'value',
  variant = 'line',
  height = 320,
}: {
  series: FileSeries[]
  field?: SeriesField
  variant?: TrendVariant
  height?: number
}) {
  const drawIn = useDrawIn()
  const { rows, keys } = useMemo(() => {
    const byPeriod = new Map<string, Record<string, unknown>>()
    const names: string[] = []
    series.forEach((entry) => {
      names.push(entry.column)
      entry.points.forEach((point) => {
        const row = byPeriod.get(point.period) ?? { period: point.period }
        row[entry.column] = variant === 'range' ? [point.min, point.max] : point[field] ?? null
        byPeriod.set(point.period, row)
      })
    })
    const ordered = [...byPeriod.values()].sort((left, right) =>
      String(left.period).localeCompare(String(right.period)))
    return { rows: ordered, keys: names }
  }, [series, field, variant])

  if (rows.length === 0) return EMPTY

  const fieldLabel: Record<SeriesField, string> = { value: 'average', sum: 'total', min: 'lowest', max: 'highest' }

  return (
    <div>
      <div style={{ height }} role="img" aria-label={`${keys.map((key) => humanise(key)).join(', ')} over time`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 6, right: 12, bottom: 4, left: -8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
            <XAxis
              dataKey="period"
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={AXIS_LINE}
              minTickGap={32}
              angle={rows.length > 40 ? -35 : 0}
              textAnchor={rows.length > 40 ? 'end' : 'middle'}
              height={rows.length > 40 ? 54 : 30}
            />
            <YAxis tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} width={62} domain={['auto', 'auto']} />
            <Tooltip content={<ChartTooltipContent valueDigits={2} />} cursor={{ stroke: 'var(--border-strong)', strokeWidth: 1 }} />
            {keys.map((key, index) => {
              const color = colorAt(index)
              const common = {
                key,
                dataKey: key,
                name: humanise(key),
                stroke: color,
                dot: false,
                connectNulls: false,
                strokeWidth: 2,
                isAnimationActive: drawIn,
                animationDuration: DRAW_MS,
                animationEasing: 'ease-out' as const,
              }
              if (variant === 'bar') {
                return (
                  <Bar
                    key={key}
                    dataKey={key}
                    name={humanise(key)}
                    fill={color}
                    stackId={keys.length > 1 ? 'total' : undefined}
                    radius={[3, 3, 0, 0]}
                    isAnimationActive={drawIn}
                    animationDuration={DRAW_MS}
                  />
                )
              }
              if (variant === 'range') {
                return (
                  <Area
                    key={key}
                    dataKey={key}
                    name={humanise(key)}
                    type="monotone"
                    stroke={color}
                    strokeWidth={1.5}
                    strokeOpacity={0.55}
                    fill={color}
                    fillOpacity={0.16}
                    isAnimationActive={drawIn}
                    animationDuration={DRAW_MS}
                  />
                )
              }
              if (variant === 'stacked') {
                return <Area {...common} type="monotone" stackId="stack" fill={color} fillOpacity={0.55} />
              }
              if (variant === 'area') return <Area {...common} type="monotone" fill={color} fillOpacity={0.14} />
              return <Line {...common} type="monotone" />
            })}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      {keys.length > 1 ? <ChartLegend items={keys.map((key, index) => ({ label: humanise(key), color: colorAt(index) }))} /> : null}
      <ChartFooter>
        {variant === 'range'
          ? `${rows.length} periods · band is the lowest to highest reading inside each period${series[0] ? ` · bucketed by ${series[0].bucket}` : ''}`
          : `${rows.length} periods · ${fieldLabel[field]} per period${variant === 'stacked' || (variant === 'bar' && keys.length > 1) ? ' · stacked' : ''}${series[0] ? ` · bucketed by ${series[0].bucket}` : ''}`}
      </ChartFooter>
    </div>
  )
}

export function FileHistogramChart({
  bins,
  label,
  height = 260,
}: {
  bins: { label: string; start: number; end: number; count: number }[]
  label: string
  height?: number
}) {
  const drawIn = useDrawIn()
  if (bins.length === 0) return EMPTY
  const data = bins.map((bin) => ({ ...bin, band: `${formatNumber(bin.start, 2)}–${formatNumber(bin.end, 2)}` }))

  return (
    <div>
      <div style={{ height }} role="img" aria-label={`Distribution of ${label}`}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 6, right: 12, bottom: 4, left: -8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
            <XAxis dataKey="band" tick={{ ...AXIS_TICK, fontSize: 10 }} tickLine={false} axisLine={AXIS_LINE} interval="preserveStartEnd" angle={-30} textAnchor="end" height={48} />
            <YAxis tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} width={54} allowDecimals={false} />
            <Tooltip content={<ChartTooltipContent />} cursor={{ fill: 'var(--surface-muted)' }} />
            <Bar dataKey="count" name="records" fill={colorAt(1)} radius={[3, 3, 0, 0]} isAnimationActive={drawIn} animationDuration={DRAW_MS} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ChartFooter>{`${bins.length} bands across ${formatNumber(bins[0].start, 2)} to ${formatNumber(bins[bins.length - 1].end, 2)}`}</ChartFooter>
    </div>
  )
}

export function FileCategoryChart({
  items,
  metricKey,
  variant = 'bar',
  height = 300,
}: {
  items: Record<string, string | number | null>[]
  metricKey: string
  variant?: CategoryVariant
  height?: number
}) {
  const drawIn = useDrawIn()
  const data = useMemo(
    () =>
      items
        .map((item) => ({ label: String(item.label ?? '—'), value: Number(item[metricKey] ?? 0) }))
        .filter((row) => Number.isFinite(row.value)),
    [items, metricKey],
  )

  if (data.length === 0) return EMPTY
  const isCount = metricKey === 'count'
  const valueName = isCount ? 'records' : humanise(metricKey.replace('mean_', ''))

  if (variant === 'pie' || variant === 'donut') {
    return (
      <div>
        <div style={{ height }} role="img" aria-label="Category share">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Tooltip content={<ChartTooltipContent />} />
              <Legend wrapperStyle={{ fontSize: 11 }} formatter={(value) => String(value)} />
              <Pie
                data={data}
                dataKey="value"
                nameKey="label"
                innerRadius={variant === 'donut' ? 62 : 0}
                outerRadius={Math.min(104, height / 2 - 16)}
                paddingAngle={variant === 'donut' ? 1 : 0}
                isAnimationActive={drawIn}
                animationDuration={DRAW_MS}
              >
                {data.map((row, index) => (
                  <Cell key={row.label} fill={colorAt(index)} stroke="var(--surface)" />
                ))}
              </Pie>
            </PieChart>
          </ResponsiveContainer>
        </div>
        <ChartFooter>{`${data.length} categories${isCount ? ' · share of records' : ` · ${humanise(metricKey.replace('mean_', ''))} by category`}`}</ChartFooter>
      </div>
    )
  }

  if (variant === 'radar') {
    return (
      <div>
        <div style={{ height }} role="img" aria-label="Category radar">
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart data={data} outerRadius="72%">
              <PolarGrid stroke={GRID_STROKE} />
              <PolarAngleAxis dataKey="label" tick={{ ...AXIS_TICK, fontSize: 11 }} />
              <PolarRadiusAxis tick={{ ...AXIS_TICK, fontSize: 10 }} axisLine={false} />
              <Tooltip content={<ChartTooltipContent />} />
              <Radar name={valueName} dataKey="value" stroke={colorAt(0)} fill={colorAt(0)} fillOpacity={0.35} isAnimationActive={drawIn} animationDuration={DRAW_MS} />
            </RadarChart>
          </ResponsiveContainer>
        </div>
        <ChartFooter>{`${data.length} categories around the ring · ${valueName}`}</ChartFooter>
      </div>
    )
  }

  if (variant === 'column') {
    return (
      <div>
        <div style={{ height }} role="img" aria-label="Category totals">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 6, right: 12, bottom: 4, left: -8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
              <XAxis dataKey="label" tick={{ ...AXIS_TICK, fontSize: 11 }} tickLine={false} axisLine={AXIS_LINE} interval={0} angle={data.length > 5 ? -25 : 0} textAnchor={data.length > 5 ? 'end' : 'middle'} height={data.length > 5 ? 52 : 30} />
              <YAxis tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} width={54} />
              <Tooltip content={<ChartTooltipContent />} cursor={{ fill: 'var(--surface-muted)' }} />
              <Bar dataKey="value" name={valueName} fill={colorAt(0)} radius={[3, 3, 0, 0]} isAnimationActive={drawIn} animationDuration={DRAW_MS}>
                {data.map((row, index) => (
                  <Cell key={row.label} fill={colorAt(index)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <ChartFooter>{`${data.length} categories shown · ${valueName}`}</ChartFooter>
      </div>
    )
  }

  return (
    <div>
      <div style={{ height }} role="img" aria-label="Category totals">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} horizontal={false} />
            <XAxis type="number" tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} />
            <YAxis type="category" dataKey="label" tick={{ ...AXIS_TICK, fontSize: 11 }} tickLine={false} axisLine={AXIS_LINE} width={110} />
            <Tooltip content={<ChartTooltipContent />} cursor={{ fill: 'var(--surface-muted)' }} />
            <Bar dataKey="value" name={valueName} fill={colorAt(0)} radius={[0, 3, 3, 0]} isAnimationActive={drawIn} animationDuration={DRAW_MS}>
              {data.map((row, index) => (
                <Cell key={row.label} fill={colorAt(index)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <ChartFooter>{`${data.length} categories shown · ${valueName}`}</ChartFooter>
    </div>
  )
}

export function FileScatterChart({ pair, height = 320 }: { pair: FileScatterPair; height?: number }) {
  const drawIn = useDrawIn()
  if (pair.points.length === 0) return EMPTY

  return (
    <div>
      <div style={{ height }} role="img" aria-label={`${humanise(pair.y)} against ${humanise(pair.x)}`}>
        <ResponsiveContainer width="100%" height="100%">
          <ScatterChart margin={{ top: 8, right: 12, bottom: 4, left: -8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
            <XAxis type="number" dataKey="x" name={humanise(pair.x)} tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} domain={['auto', 'auto']} />
            <YAxis type="number" dataKey="y" name={humanise(pair.y)} tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} width={62} domain={['auto', 'auto']} />
            <ZAxis range={[28, 28]} />
            <Tooltip content={<ChartTooltipContent />} cursor={{ strokeDasharray: '3 3' }} />
            <Scatter data={pair.points} fill={colorAt(1)} fillOpacity={0.65} isAnimationActive={drawIn} animationDuration={DRAW_MS} />
          </ScatterChart>
        </ResponsiveContainer>
      </div>
      <ChartFooter>{`${pair.points.length} paired values plotted${pair.points.length >= 250 ? ' (sampled)' : ''}`}</ChartFooter>
    </div>
  )
}

export function FileCorrelationGrid({
  columns,
  cells,
}: {
  columns: string[]
  cells: FileCorrelationCell[]
}) {
  const lookup = useMemo(() => {
    const map = new Map<string, number | null>()
    cells.forEach((cell) => {
      map.set(`${cell.row}|${cell.column}`, cell.value)
      map.set(`${cell.column}|${cell.row}`, cell.value)
    })
    return map
  }, [cells])

  if (columns.length < 2) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed py-10 text-sm muted" style={{ borderColor: 'var(--border-strong)' }}>
        At least two numeric columns are needed for a correlation matrix.
      </div>
    )
  }

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-xs">
          <caption className="sr-only">Correlation between the numeric columns of this file</caption>
          <thead>
            <tr>
              <th className="p-2" />
              {columns.map((column) => (
                <th key={column} scope="col" className="max-w-[7rem] truncate p-2 text-center font-semibold muted" title={humanise(column)}>
                  {humanise(column)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {columns.map((rowColumn) => (
              <tr key={rowColumn}>
                <th scope="row" className="max-w-[9rem] truncate p-2 text-left font-medium" title={humanise(rowColumn)}>
                  {humanise(rowColumn)}
                </th>
                {columns.map((columnColumn) => {
                  const value = lookup.get(`${rowColumn}|${columnColumn}`)
                  const diagonal = rowColumn === columnColumn
                  if (value === null || value === undefined) {
                    return (
                      <td key={columnColumn} className="p-1.5">
                        <div className="flex h-10 items-center justify-center rounded border border-dashed subtle" style={{ borderColor: 'var(--border-strong)' }}>
                          n/a
                        </div>
                      </td>
                    )
                  }
                  const magnitude = Math.min(1, Math.abs(value))
                  const rgb = value >= 0 ? '39, 99, 58' : '37, 124, 235'
                  return (
                    <td key={columnColumn} className="p-1.5">
                      <div
                        className="flex h-10 items-center justify-center rounded font-mono"
                        style={{ backgroundColor: diagonal ? 'var(--surface-muted)' : `rgba(${rgb}, ${(0.12 + magnitude * 0.72).toFixed(3)})` }}
                        title={`${humanise(rowColumn)} vs ${humanise(columnColumn)}: r=${value.toFixed(3)}`}
                      >
                        <span className={Math.abs(value) > 0.55 && !diagonal ? 'font-semibold text-white' : ''}>{value.toFixed(2)}</span>
                      </div>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs subtle">
        <span>Green positive · blue negative · intensity shows strength</span>
        <span>Correlation is not causation.</span>
      </div>
    </div>
  )
}

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** Below the yearly average in blue, above it in red, so a season reads as a band. */
function divergingColor(deviation: number, spread: number): string {
  if (!Number.isFinite(deviation) || spread <= 0) return 'var(--surface-muted)'
  const ratio = Math.max(-1, Math.min(1, deviation / spread))
  return ratio >= 0 ? `rgba(169, 74, 44, ${(0.14 + Math.abs(ratio) * 0.72).toFixed(3)})` : `rgba(46, 116, 163, ${(0.14 + Math.abs(ratio) * 0.72).toFixed(3)})`
}

export function FileHeatmapChart({ heatmap, height = 320 }: { heatmap: FileHeatmap; height?: number }) {
  if (!heatmap.cells.length) return EMPTY
  const byCell = new Map(heatmap.cells.map((cell) => [`${cell.year}-${cell.month}`, cell]))
  const spread = Math.max(1e-6, ...heatmap.cells.map((cell) => Math.abs(cell.deviation)))
  const width = `4.5rem repeat(12, minmax(1.6rem, 1fr))`

  return (
    <div>
      <div className="overflow-x-auto">
        <div className="grid min-w-[34rem] gap-1 text-[11px]" style={{ gridTemplateColumns: width, minHeight: height }}>
          <span />
          {MONTH_LABELS.map((month) => (
            <span key={month} className="pb-1 text-center font-semibold muted">{month}</span>
          ))}
          {heatmap.years.map((year) => (
            <Fragment key={year}>
              <span className="mono flex items-center justify-end pr-2 muted">{year}</span>
              {MONTH_LABELS.map((month, index) => {
                const cell = byCell.get(`${year}-${index + 1}`)
                if (!cell) {
                  return <span key={month} className="rounded border border-dashed" style={{ borderColor: 'var(--border-strong)', minHeight: '1.5rem' }} title={`${year} ${month}: no readings`} />
                }
                return (
                  <span
                    key={month}
                    className="mono flex items-center justify-center rounded font-medium"
                    style={{ backgroundColor: divergingColor(cell.deviation, spread), minHeight: '1.5rem' }}
                    title={`${year} ${month} · average ${formatNumber(cell.value, 2)} · ${formatNumber(cell.deviation, 2)} from the file mean · ${cell.count} readings`}
                  >
                    {formatNumber(cell.value, 1)}
                  </span>
                )
              })}
            </Fragment>
          ))}
        </div>
      </div>
      <ChartFooter>
        {`${humanise(heatmap.column)} averaged by month and year · ${formatNumber(heatmap.mean, 2)} overall average · bluer than the file average, redder above it · ${heatmap.years[0]}–${heatmap.years[heatmap.years.length - 1]}`}
      </ChartFooter>
    </div>
  )
}

export function FileForecastHistoryChart({ record, height = 260 }: { record: FileForecastColumn; height?: number }) {
  const drawIn = useDrawIn()
  const rows = useMemo(() => {
    const history = record.observed.map((point, index) => ({
      year: String(point.year),
      observed: point.value,
      fitted: record.fitted[index]?.value ?? null,
    }))
    return [...history, { year: String(record.target_year), observed: null, fitted: record.annual }]
  }, [record])

  if (rows.length < 2) return EMPTY

  return (
    <div>
      <div style={{ height }} role="img" aria-label={`Yearly ${humanise(record.column)} observed against the fitted trend`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 6, right: 12, bottom: 4, left: -8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
            <XAxis dataKey="year" tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} minTickGap={16} />
            <YAxis tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} width={62} domain={['auto', 'auto']} />
            <Tooltip content={<ChartTooltipContent valueDigits={2} />} cursor={{ stroke: 'var(--border-strong)', strokeWidth: 1 }} />
            <Line dataKey="observed" name="observed year" stroke={colorAt(1)} strokeWidth={2} dot={{ r: 3 }} isAnimationActive={drawIn} animationDuration={DRAW_MS} />
            <Line dataKey="fitted" name="fitted trend" stroke={colorAt(0)} strokeWidth={2} strokeDasharray="5 4" dot={false} isAnimationActive={drawIn} animationDuration={DRAW_MS} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend items={[{ label: 'observed year', color: colorAt(1) }, { label: 'fitted trend', color: colorAt(0) }]} />
      <ChartFooter>{`Trend of ${formatNumber(record.trend_per_year ?? 0, 3)} per year across ${record.observed.length} year(s), ending with the ${record.target_year} projection.`}</ChartFooter>
    </div>
  )
}

export function FileForecastMonthlyChart({ record, height = 280 }: { record: FileForecastColumn; height?: number }) {
  const drawIn = useDrawIn()
  const rows = useMemo(
    () =>
      record.monthly.map((month) => ({
        period: month.period.slice(5),
        band: [month.low, month.high] as [number | null, number | null],
        value: month.value,
      })),
    [record],
  )

  if (rows.length === 0) return EMPTY

  return (
    <div>
      <div style={{ height }} role="img" aria-label={`Monthly projection for ${record.target_year}`}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 6, right: 12, bottom: 4, left: -8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
            <XAxis dataKey="period" tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} />
            <YAxis tick={AXIS_TICK} tickLine={false} axisLine={AXIS_LINE} width={62} domain={['auto', 'auto']} />
            <Tooltip content={<ChartTooltipContent valueDigits={2} />} cursor={{ stroke: 'var(--border-strong)', strokeWidth: 1 }} />
            <Area dataKey="band" name="likely range" stroke={colorAt(0)} strokeOpacity={0.4} fill={colorAt(0)} fillOpacity={0.16} type="monotone" isAnimationActive={drawIn} animationDuration={DRAW_MS} />
            <Line dataKey="value" name={humanise(record.column)} stroke={colorAt(0)} strokeWidth={2} dot={false} isAnimationActive={drawIn} animationDuration={DRAW_MS} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <ChartLegend items={[{ label: 'projected month', color: colorAt(0) }, { label: 'likely range', color: colorAt(0) }]} />
      <ChartFooter>{`${record.target_year} monthly projection · ${humanise(record.column)} · band is the range this file's own year-to-year scatter supports.`}</ChartFooter>
    </div>
  )
}
