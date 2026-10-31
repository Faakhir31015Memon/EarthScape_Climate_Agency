import { Component, useState, type ReactNode } from 'react'
import { orthographic, type LatLon } from '../../lib/geo'
import { hasWebGL, prefersReducedMotion } from '../../lib/motion'
import { useTheme } from '../../features/theme/ThemeContext'
import StationGlobe, { type GlobeStation } from './StationGlobe'

interface GlobeHeroProps {
  stations: GlobeStation[]
  focus: LatLon | null
  regionsLabel: string
  stationsLabel: string
}

// If the WebGL path throws (driver limits, locked-down machine) the SVG projection takes
// over instead of the whole hero going blank.
class GlobeBoundary extends Component<{ fallback: ReactNode; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children
  }
}

const VIEW = 460
const RADIUS = 200

// The same coordinates projected onto a flat sheet, so the fallback needs no GL context.
function FlatGlobe({ stations, focus }: { stations: GlobeStation[]; focus: LatLon }) {
  const size = { cx: VIEW / 2, cy: VIEW / 2, radius: RADIUS }
  const visible = stations
    .map((station) => ({ station, p: orthographic(station, focus, size) }))
    .filter((entry) => entry.p.visible)

  return (
    <svg viewBox={`0 0 ${VIEW} ${VIEW}`} className="globe-flat" role="img" aria-label="Station locations plotted on a globe">
      <defs>
        <radialGradient id="globe-face" cx="42%" cy="34%" r="72%">
          <stop offset="0%" stopColor="var(--globe-lit)" />
          <stop offset="100%" stopColor="var(--globe-shade)" />
        </radialGradient>
      </defs>
      <circle cx={VIEW / 2} cy={VIEW / 2} r={RADIUS} fill="url(#globe-face)" stroke="var(--border-strong)" strokeWidth="1" />
      {[-60, -30, 0, 30, 60].map((latitude) => {
        const points = Array.from({ length: 61 }, (_, i) => orthographic({ latitude, longitude: (i / 60) * 360 - 180 }, focus, size))
        const d = points.filter((p) => p.depth > -0.02).map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')
        return d ? <path key={`p${latitude}`} d={d} fill="none" stroke="var(--text)" strokeOpacity="0.12" strokeWidth="1" /> : null
      })}
      {[0, 30, 60, 90, 120, 150].map((offset) => {
        const longitude = focus.longitude + offset - 180
        const points = Array.from({ length: 61 }, (_, i) => orthographic({ latitude: (i / 60) * 180 - 90, longitude }, focus, size))
        const d = points.filter((p) => p.depth > -0.02).map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ')
        return d ? <path key={`m${offset}`} d={d} fill="none" stroke="var(--text)" strokeOpacity="0.1" strokeWidth="1" /> : null
      })}
      {visible.map(({ station, p }) => (
        <g key={`${station.latitude}-${station.longitude}-${station.name}`}>
          <title>{`${station.name} · ${station.region}`}</title>
          <circle cx={p.x} cy={p.y} r="9" fill="none" stroke="var(--info-strong)" strokeOpacity="0.32" className="globe-ring" />
          <circle cx={p.x} cy={p.y} r="3.4" fill="var(--info-strong)" fillOpacity="0.95" />
        </g>
      ))}
    </svg>
  )
}

export function GlobeHero({ stations, focus, regionsLabel, stationsLabel }: GlobeHeroProps) {
  // Decided once, on purpose: swapping rendering paths mid-session would flash the hero.
  const [webgl] = useState(() => hasWebGL() && !prefersReducedMotion())
  const { theme } = useTheme()

  const centre: LatLon = focus ?? { latitude: 0, longitude: 0 }

  return (
    <figure className="card relative flex h-full min-h-[420px] flex-col overflow-hidden !p-0">
      <figcaption className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2.5" style={{ borderColor: 'var(--border)' }}>
        <p className="panel-title">Station network</p>
        <span className="rail-item ml-auto">
          marks <b>{stationsLabel}</b>
        </span>
        <span className="rail-item">
          regions <b>{regionsLabel}</b>
        </span>
      </figcaption>

      <div className="relative flex-1 globe-stage">
        {stations.length === 0 ? (
          <p className="absolute inset-0 flex items-center justify-center px-6 text-center text-sm muted">
            The region catalogue carries no coordinates yet, so there is nothing to plot.
          </p>
        ) : webgl ? (
          <GlobeBoundary fallback={<FlatGlobe stations={stations} focus={centre} />}>
            <StationGlobe stations={stations} focus={centre} themeKey={theme} className="absolute inset-0 h-full w-full" />
          </GlobeBoundary>
        ) : (
          <FlatGlobe stations={stations} focus={centre} />
        )}
      </div>

      <p className="border-t px-4 py-2 text-[11px] leading-relaxed subtle" style={{ borderColor: 'var(--border)' }}>
        Every mark is a location in the region catalogue, placed at its recorded coordinates. Hover a mark to read which region it belongs to.
      </p>
    </figure>
  )
}

export default GlobeHero
