import { useMemo } from 'react'
import type { CorrelationResponse } from '../../types/api'
import { humanise, metricLabel } from '../../lib/format'

function shade(coefficient: number, alpha: number): string {
  // Diverging scale: negative (hydro) ↔ positive (forest), intensity by magnitude.
  const magnitude = Math.min(1, Math.abs(coefficient))
  const rgb = coefficient >= 0 ? '39, 99, 58' : '37, 124, 235'
  return `rgba(${rgb}, ${(0.12 + magnitude * 0.72) * alpha})`
}

export function CorrelationHeatmap({ response }: { response: CorrelationResponse }) {
  const { metrics, lookup, maxSample } = useMemo(() => {
    const metrics = response.metrics
    const lookup = new Map(response.cells.map((cell) => [`${cell.x}|${cell.y}`, cell]))
    const maxSample = response.sample_size ?? Math.max(0, ...response.cells.map((cell) => cell.sample_size))
    return { metrics, lookup, maxSample }
  }, [response])

  if (metrics.length < 2 || response.cells.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed py-10 text-sm muted" style={{ borderColor: 'var(--border-strong)' }}>
        At least two metrics with overlapping observations are required to compute correlations.
      </div>
    )
  }

  const labelOf = (field: string) => response.labels?.[field] ?? metricLabel(field as never)

  return (
    <div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-xs">
          <caption className="sr-only">Pearson correlation coefficients between climate metrics</caption>
          <thead>
            <tr>
              <th className="p-2" />
              {metrics.map((metric) => (
                <th key={metric} scope="col" className="p-2 text-center font-semibold muted" title={labelOf(metric)}>
                  {humanise(metric.split('_')[0])}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {metrics.map((rowMetric) => (
              <tr key={rowMetric}>
                <th scope="row" className="whitespace-nowrap p-2 text-left font-medium">
                  {labelOf(rowMetric)}
                </th>
                {metrics.map((columnMetric) => {
                  const cell = lookup.get(`${rowMetric}|${columnMetric}`) ?? lookup.get(`${columnMetric}|${rowMetric}`)
                  if (!cell) {
                    return (
                      <td key={columnMetric} className="p-1.5">
                        <div className="flex h-11 items-center justify-center rounded border border-dashed subtle" style={{ borderColor: 'var(--border-strong)' }}>
                          n/a
                        </div>
                      </td>
                    )
                  }
                  const diagonal = rowMetric === columnMetric
                  return (
                    <td key={columnMetric} className="p-1.5">
                      <div
                        className="flex h-11 flex-col items-center justify-center rounded font-mono"
                        style={{ backgroundColor: diagonal ? 'var(--surface-muted)' : shade(cell.coefficient, 1) }}
                        title={`${labelOf(rowMetric)} vs ${labelOf(columnMetric)}: r=${cell.coefficient.toFixed(3)} (${cell.sample_size} paired observations, ${cell.strength})`}
                      >
                        <span className={Math.abs(cell.coefficient) > 0.55 ? 'font-semibold text-white' : ''}>{cell.coefficient.toFixed(2)}</span>
                        <span className={`text-[10px] ${Math.abs(cell.coefficient) > 0.55 ? 'text-white/80' : 'subtle'}`}>{cell.strength.slice(0, 3)}</span>
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
        <span>
          n = {maxSample ? maxSample.toLocaleString() : '—'} paired observations · method {response.method ?? 'pearson'}
        </span>
        <span>{response.caveat ?? 'Correlation is not causation.'}</span>
      </div>
    </div>
  )
}
