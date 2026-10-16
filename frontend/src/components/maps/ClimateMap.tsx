import { useEffect, useMemo } from "react";
import {
  CircleMarker,
  MapContainer,
  Popup,
  TileLayer,
  Tooltip as LeafletTooltip,
  useMap,
} from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import type { MapMode, MapPoint } from "../../lib/api/types";
import { formatInt, formatMetric } from "../../lib/format";
import { EmptyState, ErrorState } from "../ui/EmptyState";
import { SkeletonBlock } from "../ui/Skeleton";
import { Tabs } from "../ui/Tabs";

export interface ClimateMapProps {
  points: MapPoint[];
  mode: MapMode;
  onModeChange: (mode: MapMode) => void;
  loading?: boolean;
  error?: string | null;
  emptyReason?: string;
  height?: number;
}

const MODE_TABS = [
  { id: "markers", label: "Markers" },
  { id: "heatmap", label: "Heat" },
  { id: "choropleth", label: "Choropleth" },
];

/** Fit the viewport to the data whenever the point set changes. */
function AutoBounds({ points }: { points: MapPoint[] }) {
  const map = useMap();
  useEffect(() => {
    if (points.length === 0) return;
    if (points.length === 1) {
      const p = points[0];
      if (p) map.setView([p.latitude, p.longitude], 6);
      return;
    }
    const bounds = L.latLngBounds(
      points.map((p) => [p.latitude, p.longitude] as [number, number]),
    );
    map.fitBounds(bounds, { padding: [24, 24] });
  }, [points, map]);
  return null;
}

const DEFAULT_CENTER: [number, number] = [31.5204, 74.3587];

function radiusFor(p: MapPoint, mode: MapMode): number {
  const base = Math.sqrt(Math.max(1, p.observations)) * (mode === "heatmap" ? 4.5 : 3.2);
  return Math.min(48, Math.max(6, base));
}

function colorFor(p: MapPoint, mode: MapMode): string {
  // Water/temperature stays in the blue family; magnitude drives opacity.
  if (mode === "choropleth") {
    const t = Math.min(1, p.observations / 40);
    return `rgb(37 99 168 / ${0.25 + t * 0.5})`;
  }
  return p.temperature != null ? "rgb(37 99 168)" : "rgb(34 94 60)";
}

/**
 * Leaflet climate map over OpenStreetMap tiles. Uses CircleMarker (SVG) so no
 * default image-icon assets are required. Renders the real observation summary
 * per point and an honest "no geographic data" state when there are none.
 */
export function ClimateMap({
  points,
  mode,
  onModeChange,
  loading,
  error,
  emptyReason,
  height = 440,
}: ClimateMapProps) {
  const center = useMemo<[number, number]>(() => {
    const first = points[0];
    return first ? [first.latitude, first.longitude] : DEFAULT_CENTER;
  }, [points]);

  if (loading) {
    return (
      <div style={{ height }} className="rounded-card border border-border bg-surface p-4">
        <SkeletonBlock rows={6} />
      </div>
    );
  }

  if (error) {
    return <ErrorState message={error} />;
  }

  if (points.length === 0) {
    return (
      <EmptyState
        title="No geographic data"
        reason={emptyReason ?? "No observations carry a location in the selected window."}
      />
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs
          tabs={MODE_TABS}
          value={mode}
          onChange={(id) => onModeChange(id as MapMode)}
          ariaLabel="Map layer mode"
        />
        <p className="text-xs text-muted">
          {formatInt(points.length)} location{points.length === 1 ? "" : "s"}
        </p>
      </div>

      <div style={{ height }} role="region" aria-label="Climate observation map"
        className="overflow-hidden rounded-card border border-border">
        <MapContainer
          center={center}
          zoom={6}
          scrollWheelZoom={false}
          style={{ height: "100%", width: "100%" }}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <AutoBounds points={points} />
          {points.map((p, i) => (
            <CircleMarker
              key={`${p.region}-${i}`}
              center={[p.latitude, p.longitude]}
              radius={radiusFor(p, mode)}
              pathOptions={{
                color: mode === "choropleth" ? "rgb(var(--c-surface))" : colorFor(p, mode),
                fillColor: colorFor(p, mode),
                fillOpacity: mode === "heatmap" ? 0.45 : 0.65,
                weight: 1,
              }}
            >
              <LeafletTooltip>{p.region}</LeafletTooltip>
              <Popup>
                <div className="space-y-1 text-sm">
                  <p className="font-semibold text-content">{p.region}</p>
                  <p className="text-muted">{formatInt(p.observations)} observations</p>
                  <p>
                    Latest max temp:{" "}
                    <strong>
                      {formatMetric(p.temperature ?? null, "C", 1)}
                    </strong>
                  </p>
                  <p>
                    Latest precipitation:{" "}
                    <strong>{formatMetric(p.precipitation ?? null, "mm", 1)}</strong>
                  </p>
                  {p.anomaly_count > 0 ? (
                    <p className="text-warning">{formatInt(p.anomaly_count)} flagged anomalies</p>
                  ) : null}
                </div>
              </Popup>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>

      <MapLegend mode={mode} />
    </div>
  );
}

function MapLegend({ mode }: { mode: MapMode }) {
  return (
    <div className="flex flex-wrap items-center gap-4 rounded-lg border border-border bg-surface px-3 py-2 text-xs text-muted">
      <span className="font-medium text-content">Legend</span>
      <span className="flex items-center gap-1.5">
        <span aria-hidden="true" className="inline-block h-3 w-3 rounded-full bg-accent" />
        Temperature present
      </span>
      <span className="flex items-center gap-1.5">
        <span aria-hidden="true" className="inline-block h-3 w-3 rounded-full bg-primary" />
        Precipitation only
      </span>
      <span>
        Circle size ∝ {mode === "heatmap" ? "observation density" : "observation count"}
      </span>
    </div>
  );
}
