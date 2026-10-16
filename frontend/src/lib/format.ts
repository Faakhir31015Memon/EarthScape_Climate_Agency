import { unitSymbol } from "./units";

/**
 * Locale-stable formatters. Everything renders in `en-GB` and every datetime is
 * shown in UTC with the origin labelled, because the API emits ISO-8601 UTC
 * strings and climate data must not silently shift with the browser timezone.
 */

const LOCALE = "en-GB";

function nf(opts: Intl.NumberFormatOptions): Intl.NumberFormat {
  return new Intl.NumberFormat(LOCALE, opts);
}

export function formatNumber(
  value: number | null | undefined,
  fractionDigits = 0,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return nf({
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
    useGrouping: true,
  }).format(value);
}

/** Compact integer with thousands separators (en-GB uses commas). */
export function formatInt(value: number | null | undefined): string {
  return formatNumber(value, 0);
}

/**
 * Ratio (0–1) → percent string. Used for completeness `coverage`, where the API
 * sends 1.0 for full coverage. `scale` distinguishes a 0–1 ratio from a 0–100 value.
 */
export function formatRatio(ratio: number | null | undefined, fractionDigits = 0): string {
  if (ratio === null || ratio === undefined || Number.isNaN(ratio)) return "—";
  return `${formatNumber(ratio * 100, fractionDigits)}%`;
}

/** A measurement with its API-provided unit token, e.g. 24.3 + "C" → "24.3 °C". */
export function formatMetric(
  value: number | null | undefined,
  unitToken?: string,
  fractionDigits = 1,
): string {
  const num = formatNumber(value, fractionDigits);
  if (num === "—") return num;
  const symbol = unitSymbol(unitToken);
  return symbol ? `${num} ${symbol}` : num;
}

function toUtcParts(iso: string): { date: Date; valid: boolean } {
  const date = new Date(iso);
  return { date, valid: !Number.isNaN(date.getTime()) };
}

/** Full UTC datetime with the origin labelled: "20 Sep 2026, 00:00 UTC". */
export function formatDateTimeUtc(iso: string | null | undefined): string {
  if (!iso) return "—";
  const { date, valid } = toUtcParts(iso);
  if (!valid) return iso; // show the raw value rather than a misleading dash
  const stamp = new Intl.DateTimeFormat(LOCALE, {
    timeZone: "UTC",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
  return `${stamp} UTC`;
}

/** Calendar date in UTC: "20 Sep 2026". */
export function formatDateUtc(iso: string | null | undefined): string {
  if (!iso) return "—";
  const { date, valid } = toUtcParts(iso);
  if (!valid) return iso;
  return new Intl.DateTimeFormat(LOCALE, {
    timeZone: "UTC",
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(date);
}

/** The ISO instant exactly as stored (unambiguous, machine-readable). */
export function formatIsoInstant(iso: string | null | undefined): string {
  return iso ? iso : "—";
}

/** Human duration from seconds, e.g. 90 → "1 min 30 s"; null → dash. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  if (seconds < 1) return `${formatNumber(seconds * 1000, 0)} ms`;
  if (seconds < 60) return `${formatNumber(seconds, seconds < 10 ? 2 : 0)} s`;
  const mins = Math.floor(seconds / 60);
  const rem = Math.round(seconds % 60);
  if (mins < 60) return rem ? `${mins} min ${rem} s` : `${mins} min`;
  const hours = Math.floor(mins / 60);
  const remMin = mins % 60;
  return remMin ? `${hours} h ${remMin} min` : `${hours} h`;
}

/** Byte size → human units (data lake totals), binary-ish but labelled plainly. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${formatNumber(value, i === 0 ? 0 : 1)} ${units[i]}`;
}

export function titleCase(input: string | null | undefined): string {
  if (!input) return "";
  return input
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}
