/**
 * Shared chart contracts. A chart is a pure function of {series, loading,
 * error, emptyReason} — it never fabricates points. When `points` is empty the
 * frame renders an honest EmptyState instead of an axis with no data.
 */

/** One datum on the categorical/ordinal axis plus any number of series values. */
export type ChartPoint = Record<string, number | string | null>;

export interface SeriesDef {
  /** Key looked up on each ChartPoint. */
  key: string;
  /** Human label for legend/tooltip. */
  name: string;
  /** Explicit colour (hex/rgb). Omit to use theme-aware series order. */
  color?: string;
  /** Display unit from the API (e.g. "C", "mm") appended in tooltips. */
  unit?: string;
}

export interface ChartStateProps {
  points: ChartPoint[];
  loading?: boolean;
  error?: string | null;
  /** Shown when there are no points; quote the API `reason` verbatim. */
  emptyReason?: string;
  emptyTitle?: string;
  height?: number;
}
