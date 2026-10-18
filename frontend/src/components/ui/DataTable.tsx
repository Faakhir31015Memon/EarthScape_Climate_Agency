import type { ReactNode } from 'react'
import { cn } from '../../lib/cn'

export interface Column<Row> {
  key: string
  header: ReactNode
  cell: (row: Row, index: number) => ReactNode
  align?: 'left' | 'right' | 'center'
  width?: string
  sortable?: boolean
  sortKey?: string
}

export function DataTable<Row>({
  columns,
  rows,
  rowKey,
  loading = false,
  empty,
  onRowClick,
  sort,
  onSort,
  caption,
  compact = false,
}: {
  columns: Column<Row>[]
  rows: Row[]
  rowKey: (row: Row, index: number) => string
  loading?: boolean
  empty?: ReactNode
  onRowClick?: (row: Row) => void
  sort?: { key: string; direction: 'asc' | 'desc' }
  onSort?: (key: string) => void
  caption?: string
  compact?: boolean
}) {
  return (
    <div className="table-wrap">
      <table className={cn('data', compact && 'text-[13px]')}>
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr>
            {columns.map((column) => (
              <th
                key={column.key}
                scope="col"
                style={column.width ? { width: column.width } : undefined}
                className={cn(
                  column.align === 'right' && 'text-right',
                  column.align === 'center' && 'text-center',
                  column.sortable && onSort && 'cursor-pointer select-none',
                )}
                aria-sort={sort && sort.key === (column.sortKey ?? column.key) ? (sort.direction === 'asc' ? 'ascending' : 'descending') : undefined}
                onClick={column.sortable && onSort ? () => onSort(column.sortKey ?? column.key) : undefined}
              >
                {column.header}
                {sort && column.sortable && sort.key === (column.sortKey ?? column.key) ? (
                  <span aria-hidden="true">{sort.direction === 'asc' ? ' ↑' : ' ↓'}</span>
                ) : null}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {loading ? (
            <tr>
              <td colSpan={columns.length} className="py-10 text-center">
                <span className="muted text-sm">Loading…</span>
              </td>
            </tr>
          ) : rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="py-10 text-center">
                <span className="muted text-sm">{empty ?? 'No records match the current filters.'}</span>
              </td>
            </tr>
          ) : (
            rows.map((row, index) => (
              <tr
                key={rowKey(row, index)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={cn(onRowClick && 'cursor-pointer')}
                tabIndex={onRowClick ? 0 : undefined}
                onKeyDown={
                  onRowClick
                    ? (event) => {
                        if (event.key === 'Enter') onRowClick(row)
                      }
                    : undefined
                }
              >
                {columns.map((column) => (
                  <td
                    key={column.key}
                    className={cn(column.align === 'right' && 'text-right', column.align === 'center' && 'text-center')}
                  >
                    {column.cell(row, index)}
                  </td>
                ))}
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  )
}
