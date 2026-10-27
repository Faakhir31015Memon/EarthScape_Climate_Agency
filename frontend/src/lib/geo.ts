const DEG = Math.PI / 180

export interface LatLon {
  latitude: number
  longitude: number
}

/** Spherical to cartesian: the north pole sits on +Y and the focus longitude faces +Z. */
export function spherePoint({ latitude, longitude }: LatLon, radius: number) {
  const phi = (90 - latitude) * DEG
  const theta = (longitude + 90) * DEG
  const sinPhi = Math.sin(phi)
  return {
    x: radius * sinPhi * Math.cos(theta),
    y: radius * Math.cos(phi),
    z: radius * sinPhi * Math.sin(theta),
  }
}

/**
 * Rotates a point so the given focus sits at the centre of the view, then drops the
 * depth axis. ``visible`` is false for the far half of the sphere, which the SVG
 * rendering has no depth cue for.
 */
export function orthographic(
  point: LatLon,
  focus: LatLon,
  size: { cx: number; cy: number; radius: number },
) {
  const p = spherePoint(point, 1)
  const cosLon = Math.cos(focus.longitude * DEG)
  const sinLon = Math.sin(focus.longitude * DEG)
  const x1 = p.x * cosLon - p.z * sinLon
  const z1 = p.x * sinLon + p.z * cosLon
  const cosLat = Math.cos(focus.latitude * DEG)
  const sinLat = Math.sin(focus.latitude * DEG)
  const y2 = p.y * cosLat + z1 * sinLat
  const z2 = -p.y * sinLat + z1 * cosLat
  return {
    x: size.cx + size.radius * x1,
    y: size.cy - size.radius * y2,
    depth: z2,
    visible: z2 > 0,
  }
}

/**
 * The middle of a set of coordinates, used to orient the globe so the network — not
 * empty ocean — is what the visitor sees first. Null when nothing carries a position.
 */
export function meanCentre(points: LatLon[]): LatLon | null {
  if (!points.length) return null
  let x = 0
  let y = 0
  let z = 0
  for (const point of points) {
    const v = spherePoint(point, 1)
    x += v.x
    y += v.y
    z += v.z
  }
  const lon = Math.atan2(z, x) / DEG - 90
  const lat = Math.asin(Math.max(-1, Math.min(1, y / Math.sqrt(x * x + y * y + z * z)))) / DEG
  return { latitude: round3(lat), longitude: round3(((lon + 540) % 360) - 180) }
}

function round3(value: number): number {
  return Math.round(value * 1000) / 1000
}

/** Haversine, in degrees of arc — cheap and only used to keep close stations apart on screen. */
export function angularDistance(a: LatLon, b: LatLon): number {
  const dLat = (b.latitude - a.latitude) * DEG
  const dLon = (b.longitude - a.longitude) * DEG
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(a.latitude * DEG) * Math.cos(b.latitude * DEG) * Math.sin(dLon / 2) ** 2
  return 2 * Math.asin(Math.min(1, Math.sqrt(h))) / DEG
}
