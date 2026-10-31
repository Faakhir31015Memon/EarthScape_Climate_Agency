import { useEffect, useMemo } from 'react'
import { CircleMarker, MapContainer, Popup, TileLayer, Tooltip as LeafletTooltip, useMap } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import type { FileGeo } from '../../types/api'
import { formatNumber, humanise } from '../../lib/format'
import { colorForValue } from './ClimateMap'

/**
 * The latitude/longitude columns of an uploaded file plotted as a map. Bubble size is how
 * many readings sat at that spot; bubble colour is the average of the measure the backend chose.
 */
export function FileMapChart({ geo, height = 380 }: { geo: FileGeo; height?: number }) {
  const { min, max } = useMemo(() => {
    const values = geo.points.map((point) => point.value).filter((value): value is number => value !== null)
    if (values.length < 2) return { min: 0, max: 1 }
    return { min: Math.min(...values), max: Math.max(...values) }
  }, [geo.points])

  const peak = Math.max(1, ...geo.points.map((point) => point.count))

  return (
    <div className="relative" style={{ height }}>
      <MapContainer
        center={[24, 68]}
        zoom={3}
        scrollWheelZoom
        style={{ height: '100%', width: '100%', borderRadius: '0.75rem' }}
        worldCopyJump
      >
        <TileLayer attribution='&copy; OpenStreetMap contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <FitToPoints points={geo.points} />
        {geo.points.map((point) => (
          <CircleMarker
            key={`${point.lat},${point.lng}`}
            center={[point.lat, point.lng]}
            radius={5 + Math.round((point.count / peak) * 9)}
            pathOptions={{
              color: 'var(--surface)',
              weight: 1,
              fillColor: colorForValue(point.value, min, max),
              fillOpacity: point.value === null ? 0.25 : 0.8,
            }}
          >
            <LeafletTooltip direction="top" offset={[0, -6]}>
              {point.label ?? `${point.lat.toFixed(2)}, ${point.lng.toFixed(2)}`}
              {point.value === null ? '' : `: ${formatNumber(point.value, 1)}`}
            </LeafletTooltip>
            <Popup>
              <div className="space-y-1 text-xs">
                <p className="text-sm font-semibold">{point.label ?? 'Unnamed site'}</p>
                <p className="mono">
                  {point.lat.toFixed(3)}, {point.lng.toFixed(3)}
                </p>
                <p className="muted">
                  {point.count} reading{point.count === 1 ? '' : 's'}
                  {geo.measure ? ` · average ${humanise(geo.measure)} ${point.value === null ? 'unavailable' : formatNumber(point.value, 2)}` : ''}
                </p>
              </div>
            </Popup>
          </CircleMarker>
        ))}
      </MapContainer>

      <div className="card-tight absolute left-3 top-3 z-[400] flex flex-wrap items-center gap-2 text-xs">
        <span className="font-semibold">{geo.measure ? humanise(geo.measure) : 'Sites'}</span>
        <span className="subtle">· {geo.points.length} location(s)</span>
        <span className="mono">{formatNumber(min, 1)}</span>
        <span
          className="h-2 w-24 rounded-full"
          style={{ background: `linear-gradient(90deg, ${colorForValue(min, min, max)}, ${colorForValue((min + max) / 2, min, max)}, ${colorForValue(max, min, max)})` }}
          aria-hidden="true"
        />
        <span className="mono">{formatNumber(max, 1)}</span>
      </div>
    </div>
  )
}

function FitToPoints({ points }: { points: FileGeo['points'] }) {
  const map = useMap()
  useEffect(() => {
    if (points.length === 0) return
    const lats = points.map((point) => point.lat)
    const lngs = points.map((point) => point.lng)
    map.fitBounds(
      [
        [Math.min(...lats), Math.min(...lngs)],
        [Math.max(...lats), Math.max(...lngs)],
      ],
      { padding: [24, 24], maxZoom: 8 },
    )
  }, [points, map])
  return null
}
