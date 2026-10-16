import type { ClimateVariable } from "./api/types";

/**
 * Metric display units. The authoritative unit for an observation always comes
 * from the API (`metrics.<var>.unit`); this module only decorates that token
 * (e.g. provider sends "C" → we render "°C"). Nothing here hardcodes a value's
 * unit — call sites must pass the API-provided token.
 */

/** Maps provider unit tokens to a human-readable metric symbol. */
const UNIT_SYMBOLS: Record<string, string> = {
  C: "°C",
  "°C": "°C",
  mm: "mm",
  "%": "%",
  "m/s": "m/s",
  hPa: "hPa",
  km_h: "km/h",
  "W/m2": "W/m²",
  "W/m²": "W/m²",
  deg: "°",
};

export function unitSymbol(unitToken: string | undefined): string {
  if (!unitToken) return "";
  return UNIT_SYMBOLS[unitToken] ?? unitToken;
}

/** Human labels for the canonical climate variables. */
export const VARIABLE_LABELS: Record<string, string> = {
  temperature: "Temperature",
  temperature_max: "Max temperature",
  temperature_min: "Min temperature",
  precipitation: "Precipitation",
  humidity: "Humidity",
  wind_speed: "Wind speed",
  wind_direction: "Wind direction",
  pressure: "Pressure",
  cloud_cover: "Cloud cover",
  solar_radiation: "Solar radiation",
  snow: "Snow",
};

export function variableLabel(variable: string): string {
  return VARIABLE_LABELS[variable] ?? variable.replace(/_/g, " ");
}

/** Is this variable a temperature family member (drives the blue series)? */
export function isTemperatureVariable(variable: string): boolean {
  return variable.startsWith("temperature");
}

/** Recommended series role, used to pick a consistent chart colour. */
export function variableSeriesRole(variable: string): ClimateVariable | string {
  if (variable.startsWith("temperature")) return "temperature";
  if (variable === "precipitation" || variable === "snow") return "precipitation";
  return variable;
}
