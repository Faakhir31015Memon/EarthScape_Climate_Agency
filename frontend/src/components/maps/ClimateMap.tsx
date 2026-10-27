import { useEffect, useMemo, useState } from 'react'
import { CircleMarker, MapContainer, Marker, Popup, TileLayer, Tooltip as LeafletTooltip, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { Layers, Maximize2 } from 'lucide-react'
import type { MapDataResponse, MapPoint, Metric } from '../../types/api'
import { formatNumber, metricLabel, relativeTime } from '../../lib/format'

const COLD = [38, 90, 118]
const HOT = [169, 59, 44]
const GREEN = [53, 83, 47]

function interpolate(from: number[], to: number[], ratio: number): string {
  const channel = from.map((value, index) => Math.round(value + (to[index] - value) * ratio))
  return `rgb(${channel.join(',')})`
}

export function colorForValue(value: number | null, min: number, max: number, metric?: Metric): string {
  if (value === null || !Number.isFinite(value)) return 'rgba(137, 145, 137, 0.5)'
  const span = max - min || 1
  const ratio = Math.max(0, Math.min(1, (value - min) / span))
  if (metric?.startsWith('temperature')) return interpolate(COLD, HOT, ratio)
  if (metric === 'precipitation_mm') return interpolate([207, 226, 235], [32, 72, 94], ratio)
  return interpolate([216, 231, 208], GREEN, ratio)
}

function FitBounds({ bounds }: { bounds: [number, number, number, number] | null }) {
  const map = useMap()
  useEffect(() => {
    if (!bounds) return
    map.fitBounds(
      [
        [bounds[0], bounds[1]],
        [bounds[2], bounds[3]],
      ],
      { padding: [24, 24], maxZoom: 6 },
    )
  }, [bounds, map])
  return null
}

function ZoomWatcher({ onZoom }: { onZoom: (zoom: number) => void }) {
  const map = useMap()
  useEffect(() => {
    const handler = () => onZoom(map.getZoom())
    handler()
    map.on('zoomend', handler)
    return () => {
      map.off('zoomend', handler)
    }
  }, [map, onZoom])
  return null
}

export interface ClimateMapProps {
  data: Pick<MapDataResponse, 'points' | 'clusters' | 'bounds' | 'metrics' | 'data_note'>
  metric: Metric
  unit: string
  height?: number
  onSelectPoint?: (point: MapPoint) => void
  detailZoom?: number
}

export function ClimateMap({ data, metric, unit, height = 520, onSelectPoint, detailZoom = 5 }: ClimateMapProps) {
  const [zoom, setZoom] = useState(3)
  const [showClusters, setShowClusters] = useState(true)

  const { min, max } = useMemo(() => {
    const values = data.points
      .map((point) => point.measurements[metric]?.value ?? null)
      .filter((value): value is number => value !== null && Number.isFinite(value))
    if (!values.length) return { min: 0, max: 1 }
    return { min: Math.min(...values), max: Math.max(...values) }
  }, [data.points, metric])

  const useDetail = zoom >= detailZoom || !showClusters || data.clusters.length === 0

  return (
    <div className="relative" style={{ height }}>
      <MapContainer
        center={[24, 68]}
        zoom={3}
        scrollWheelZoom
        style={{ height: '100%', width: '100%', borderRadius: '0.75rem' }}
        worldCopyJump
      >
        <TileLayer
          attribution='&copy; OpenStreetMap contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <FitBounds bounds={data.bounds} />
        <ZoomWatcher onZoom={setZoom} />

        {useDetail
          ? data.points.map((point) => {
              const lat = point.latitude
              const lng = point.longitude
              if (lat === null || lng === null) return null
              const measurement = point.measurements[metric]
              const value = measurement?.value ?? null
              return (
                <CircleMarker
                  key={`${point.location_code}-${point.last_observation_date}`}
                  center={[lat, lng]}
                  radius={value === null ? 4 : 6}
                  pathOptions={{
                    color: 'var(--surface)',
                    weight: 1,
                    fillColor: colorForValue(value, min, max, metric),
                    fillOpacity: value === null ? 0.25 : 0.85,
                  }}
                  eventHandlers={{ click: () => onSelectPoint?.(point) }}
                >
                  <LeafletTooltip direction="top" offset={[0, -6]}>
                    {point.location_name}: {value === null ? 'no value' : `${formatNumber(value, 1)} ${unit}`}
                  </LeafletTooltip>
                  <Popup>
                    <div className="space-y-1 text-xs" style={{ minWidth: '12rem' }}>
                      <p className="text-sm font-semibold">{point.location_name}</p>
                      <p className="muted">
                        {point.region_code} · {point.country}
                      </p>
                      <p className="mono">
                        {lat.toFixed(3)}, {lng.toFixed(3)}
                      </p>
                      <ul className="mt-2 space-y-1">
                        {Object.entries(point.measurements)
                          .filter(([, entry]) => entry && entry.value !== null)
                          .map(([field, entry]) => (
                            <li key={field} className="flex justify-between gap-3">
                              <span className="muted">{metricLabel(field)}</span>
                              <span className="mono font-medium">
                                {formatNumber(entry?.value ?? null, 1)} {entry?.unit ?? ''}
                              </span>
                            </li>
                          ))}
                      </ul>
                      <p className="pt-1 subtle">
                        {point.source_name ? `${point.source_name} · ` : ''}
                        {relativeTime(point.last_observation_date)}
                      </p>
                      {point.quality_flags?.length ? <p className="text-alert-high">Flags: {point.quality_flags.join(', ')}</p> : null}
                      {onSelectPoint ? (
                        <button type="button" className="link mt-1" onClick={() => onSelectPoint(point)}>
                          Open location history
                        </button>
                      ) : null}
                    </div>
                  </Popup>
                </CircleMarker>
              )
            })
          : data.clusters.map((cluster) => {
              const centroid = cluster.centroid
              if (!centroid) return null
              const clusterMean = cluster.measurements[metric]?.mean ?? null
              const icon = L.divIcon({
                className: 'earthscape-marker',
                html: `<div style="display:flex;align-items:center;justify-content:center;width:100%;height:100%;border-radius:9999px;font:600 12px 'IBM Plex Sans',system-ui;color:#fff;background:${colorForValue(
                  clusterMean,
                  min,
                  max,
                  metric,
                )};box-shadow:0 2px 8px rgba(0,0,0,.25);border:2px solid rgba(255,255,255,.8)">${cluster.point_count}</div>`,
                iconSize: [Math.min(46, 22 + cluster.point_count), Math.min(46, 22 + cluster.point_count)],
              })
              return (
                <Marker
                  key={cluster.cluster_key}
                  position={[centroid[0], centroid[1]]}
                  icon={icon}
                  eventHandlers={{ click: () => setShowClusters(false) }}
                >
                  <Popup>
                    <div className="text-xs">
                      <p className="font-semibold">{cluster.point_count} locations grouped here</p>
                      <p className="muted">{cluster.cluster_key}</p>
                      <p className="mt-1 subtle">Zoom in or click to list the underlying stations.</p>
                    </div>
                  </Popup>
                </Marker>
              )
            })}
      </MapContainer>

      <div className="card-tight absolute left-3 top-3 z-[400] space-y-2 text-xs">
        <div className="flex items-center gap-2">
          <Layers className="h-3.5 w-3.5 muted" aria-hidden="true" />
          <span className="font-semibold">{metricLabel(metric)}</span>
          <span className="subtle">· {useDetail ? `${data.points.length} stations` : `${data.clusters.length} clusters`}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="mono">{formatNumber(min, 1)}</span>
          <span
            className="h-2 w-28 rounded-full"
            style={{
              background: `linear-gradient(90deg, ${colorForValue(min, min, max, metric)}, ${colorForValue((min + max) / 2, min, max, metric)}, ${colorForValue(max, min, max, metric)})`,
            }}
            aria-hidden="true"
          />
          <span className="mono">
            {formatNumber(max, 1)} {unit}
          </span>
        </div>
      </div>

      {zoom < detailZoom && data.clusters.length > 0 ? (
        <button
          type="button"
          className="btn btn-secondary btn-sm absolute right-3 top-3 z-[400]"
          onClick={() => setShowClusters((current) => !current)}
        >
          <Maximize2 className="h-3.5 w-3.5" /> {showClusters ? 'Show stations' : 'Cluster view'}
        </button>
      ) : null}
    </div>
  )
}
