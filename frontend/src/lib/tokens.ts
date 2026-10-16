/**
 * Design tokens as runtime constants. Colours resolve through the CSS custom
 * properties defined in index.css, so a chart or canvas consumer reads the
 * *current* theme value instead of a hardcoded hex.
 */

/** Consistent 4/8px spacing scale (also mirrored in the Tailwind config). */
export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
  "2xl": 48,
} as const;

export const radii = {
  sm: 6,
  card: 12,
  full: 9999,
} as const;

/** CSS variable names for the theme-aware series/semantic colours. */
export const chartTokens = {
  temperature: "--c-chart-temperature",
  precipitation: "--c-chart-precipitation",
  series3: "--c-chart-series3",
  series4: "--c-chart-series4",
  series5: "--c-chart-series5",
} as const;

/** Deterministic series colour order (water/temperature blue leads, then earth tones). */
export const seriesTokens: readonly string[] = [
  chartTokens.temperature,
  chartTokens.precipitation,
  chartTokens.series3,
  chartTokens.series4,
  chartTokens.series5,
];

export const semanticTokens = {
  success: "--c-success",
  warning: "--c-warning",
  danger: "--c-danger",
  info: "--c-info",
  primary: "--c-primary",
  muted: "--c-muted",
} as const;

export type SeriesRole = "temperature" | "precipitation";

/**
 * Resolve a CSS custom property (an "R G B" channel triplet) to a usable
 * `rgb()` string for the active theme. Falls back to the provided colour when
 * the variable is unset (e.g. during SSR/test without a document).
 */
export function resolveCssColor(varName: string, fallback = "rgb(34 94 60)"): string {
  if (typeof document === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(varName)
    .trim();
  if (!raw) return fallback;
  // Values are stored as space-separated channels: "34 94 60".
  const channels = raw.split(/\s+/);
  if (channels.length >= 3) return `rgb(${channels[0]} ${channels[1]} ${channels[2]})`;
  return raw;
}

/** nth series colour by index, theme-aware. */
export function seriesColor(index: number): string {
  const token = seriesTokens[index % seriesTokens.length] ?? chartTokens.temperature;
  return resolveCssColor(token);
}
