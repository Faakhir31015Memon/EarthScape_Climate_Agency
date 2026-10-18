import type { CSSProperties } from 'react'

export function Skeleton({ className = '', style }: { className?: string; style?: CSSProperties }) {
  return <div className={`skeleton ${className}`} style={style} aria-hidden="true" />
}

export function CardSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="card">
      <Skeleton className="h-4 w-32" />
      <div className="mt-4 space-y-2.5">
        {Array.from({ length: lines }).map((_, index) => (
          <Skeleton key={index} className={index === lines - 1 ? 'h-4 w-2/3' : 'h-4'} />
        ))}
      </div>
    </div>
  )
}

export function TableSkeleton({ rows = 6, columns = 5 }: { rows?: number; columns?: number }) {
  return (
    <div className="table-wrap">
      <table className="data" aria-busy="true">
        <thead>
          <tr>
            {Array.from({ length: columns }).map((_, index) => (
              <th key={index}>
                <Skeleton className="h-3 w-20" />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }).map((_, rowIndex) => (
            <tr key={rowIndex}>
              {Array.from({ length: columns }).map((__, colIndex) => (
                <td key={colIndex}>
                  <Skeleton className="h-3.5 w-full" />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function ChartSkeleton({ height = 280 }: { height?: number }) {
  return <Skeleton style={{ height }} className="w-full" />
}
