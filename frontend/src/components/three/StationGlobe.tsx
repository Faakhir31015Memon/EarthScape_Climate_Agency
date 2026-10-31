import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { spherePoint, type LatLon } from '../../lib/geo'
import { prefersReducedMotion } from '../../lib/motion'

// Colours here are raw CSS hex; with management off, shaders and built-in materials
// write the same values the rest of the page uses.
THREE.ColorManagement.enabled = false

export interface GlobeStation {
  name: string
  latitude: number
  longitude: number
  region: string
}

interface StationGlobeProps {
  stations: GlobeStation[]
  focus: LatLon
  themeKey?: string
  className?: string
}

const FIELD_DOTS = 1650
const GOLDEN_ANGLE = 2.399963229728653
// Swinging the whole sphere would show ocean most of the time, so it drifts instead.
const DRIFT_AMPLITUDE = 0.3
const DRIFT_RATE = 0.13

const POINT_VERTEX = /* glsl */ `
  attribute float aSize;
  attribute float aPhase;
  attribute float aShade;
  uniform float uTime;
  uniform float uMotion;
  uniform float uPixelRatio;
  uniform float uEntrance;
  varying float vShade;
  varying float vPulse;
  void main() {
    vec4 viewed = modelViewMatrix * vec4(position, 1.0);
    float beat = sin(uTime * 1.35 + aPhase * 6.2831853);
    vPulse = 0.5 + 0.5 * beat;
    vShade = aShade;
    float size = aSize * (1.0 + 0.16 * uMotion * beat) * uEntrance;
    gl_PointSize = size * uPixelRatio * (4.4 / max(0.25, -viewed.z));
    gl_Position = projectionMatrix * viewed;
  }
`

const FIELD_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  varying float vShade;
  varying float vPulse;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    if (d > 0.5) discard;
    float falloff = 1.0 - smoothstep(0.0, 0.5, d);
    float alpha = falloff * uOpacity * (0.45 + 0.55 * vShade) * (0.82 + 0.18 * vPulse);
    gl_FragColor = vec4(uColor * (0.7 + 0.5 * vShade), alpha);
  }
`

const BEACON_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uOpacity;
  varying float vShade;
  varying float vPulse;
  void main() {
    float d = length(gl_PointCoord - 0.5);
    if (d > 0.5) discard;
    float core = 1.0 - smoothstep(0.10, 0.20, d);
    float ring = (1.0 - smoothstep(0.36, 0.46, d)) * smoothstep(0.24, 0.34, d);
    float alpha = core * 0.95 + ring * (0.25 + 0.75 * vPulse);
    gl_FragColor = vec4(uColor, alpha * uOpacity);
  }
`

const HALO_VERTEX = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vec4 viewed = modelViewMatrix * vec4(position, 1.0);
    vView = viewed.xyz;
    gl_Position = projectionMatrix * viewed;
  }
`

const HALO_FRAGMENT = /* glsl */ `
  uniform vec3 uColor;
  uniform float uStrength;
  varying vec3 vNormal;
  varying vec3 vView;
  void main() {
    // Exponent tuned by eye: higher leaves a gap at the limb, lower washes over the shell.
    float rim = pow(clamp(1.0 - abs(dot(normalize(vNormal), normalize(-vView))), 0.0, 1.0), 1.55);
    gl_FragColor = vec4(uColor, rim * uStrength);
  }
`

const BODY_VERTEX = /* glsl */ `
  varying vec3 vNormal;
  varying vec3 vView;
  varying float vHeight;
  void main() {
    vNormal = normalize(normalMatrix * normal);
    vHeight = position.y;
    vec4 viewed = modelViewMatrix * vec4(position, 1.0);
    vView = viewed.xyz;
    gl_Position = projectionMatrix * viewed;
  }
`

const BODY_FRAGMENT = /* glsl */ `
  uniform vec3 uTop;
  uniform vec3 uBottom;
  uniform vec3 uLimb;
  varying vec3 vNormal;
  varying vec3 vView;
  varying float vHeight;
  void main() {
    vec3 normal = normalize(vNormal);
    vec3 base = mix(uBottom, uTop, clamp(vHeight * 0.51 + 0.5, 0.0, 1.0));
    float lambert = clamp(dot(normal, normalize(vec3(-0.42, 0.58, 0.70))), 0.0, 1.0);
    vec3 lit = base * (0.82 + 0.26 * lambert);
    float rim = pow(clamp(1.0 - abs(dot(normal, normalize(-vView))), 0.0, 1.0), 2.2);
    gl_FragColor = vec4(mix(lit, uLimb, rim * 0.62), 1.0);
  }
`

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3)
}

function cssColor(host: HTMLElement, token: string, fallback: string): THREE.Color {
  const raw = getComputedStyle(host).getPropertyValue(token).trim()
  return new THREE.Color(raw || fallback)
}

export default function StationGlobe({ stations, focus, themeKey, className }: StationGlobeProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const readoutRef = useRef<HTMLDivElement>(null)
  const applyColorsRef = useRef<(() => void) | null>(null)

  // The class lands on <html> in the same commit, so repaint the scene on the next frame.
  useEffect(() => {
    const id = requestAnimationFrame(() => applyColorsRef.current?.())
    return () => cancelAnimationFrame(id)
  }, [themeKey])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return undefined

    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' })
    } catch {
      // No usable context; GlobeHero renders the SVG projection instead.
      host.dataset.globeFailed = 'true'
      return undefined
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.8))
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace
    renderer.setClearColor(0x000000, 0)
    renderer.domElement.style.display = 'block'
    renderer.domElement.style.width = '100%'
    renderer.domElement.style.height = '100%'
    host.appendChild(renderer.domElement)

    const reduced = prefersReducedMotion()
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(32, 1, 0.1, 50)
    camera.position.set(0, 0, 4.4)

    const root = new THREE.Group()
    scene.add(root)
    const tilt = new THREE.Group()
    const spin = new THREE.Group()
    tilt.add(spin)
    root.add(tilt)
    const focusLatRad = (focus.latitude * Math.PI) / 180
    const focusLonRad = (focus.longitude * Math.PI) / 180
    tilt.rotation.x = focusLatRad
    spin.rotation.y = focusLonRad

    const uniforms = {
      uTime: { value: 0 },
      uMotion: { value: reduced ? 0 : 1 },
      uPixelRatio: { value: renderer.getPixelRatio() },
      uEntrance: { value: reduced ? 1 : 0 },
    }

    // Occluder sphere; without it the far half of the point cloud shows through.
    const bodyUniforms = {
      uTop: { value: new THREE.Color('#fffaf1') },
      uBottom: { value: new THREE.Color('#ded0b6') },
      uLimb: { value: new THREE.Color('#a8946f') },
    }
    const core = new THREE.Mesh(new THREE.SphereGeometry(0.988, 64, 44), new THREE.ShaderMaterial({
      uniforms: bodyUniforms,
      vertexShader: BODY_VERTEX,
      fragmentShader: BODY_FRAGMENT,
    }))
    spin.add(core)

    // Fibonacci-sampled point cloud; even spacing, no pile-up at the poles.
    const fieldPosition = new Float32Array(FIELD_DOTS * 3)
    const fieldSize = new Float32Array(FIELD_DOTS)
    const fieldPhase = new Float32Array(FIELD_DOTS)
    const fieldShade = new Float32Array(FIELD_DOTS)
    for (let i = 0; i < FIELD_DOTS; i += 1) {
      const t = (i + 0.5) / FIELD_DOTS
      const y = 1 - 2 * t
      const ring = Math.sqrt(Math.max(0, 1 - y * y))
      const angle = i * GOLDEN_ANGLE
      const jitter = 1 + (Math.random() - 0.5) * 0.008
      fieldPosition[i * 3] = Math.cos(angle) * ring * jitter
      fieldPosition[i * 3 + 1] = y * jitter
      fieldPosition[i * 3 + 2] = Math.sin(angle) * ring * jitter
      fieldSize[i] = 1.9 + Math.random() * 1.5
      fieldPhase[i] = Math.random()
      fieldShade[i] = Math.random()
    }
    const fieldGeometry = new THREE.BufferGeometry()
    fieldGeometry.setAttribute('position', new THREE.BufferAttribute(fieldPosition, 3))
    fieldGeometry.setAttribute('aSize', new THREE.BufferAttribute(fieldSize, 1))
    fieldGeometry.setAttribute('aPhase', new THREE.BufferAttribute(fieldPhase, 1))
    fieldGeometry.setAttribute('aShade', new THREE.BufferAttribute(fieldShade, 1))
    const fieldMaterial = new THREE.ShaderMaterial({
      uniforms: { ...uniforms, uColor: { value: new THREE.Color('#b15a2e') }, uOpacity: { value: 0.68 } },
      vertexShader: POINT_VERTEX,
      fragmentShader: FIELD_FRAGMENT,
      transparent: true,
      depthWrite: false,
    })
    const field = new THREE.Points(fieldGeometry, fieldMaterial)
    spin.add(field)

    // Graticule: meridians every 30 degrees, parallels every 30 from -60 to 60.
    const graticule: number[] = []
    const rule = (points: Array<{ x: number; y: number; z: number }>) => {
      for (let i = 0; i < points.length - 1; i += 1) {
        graticule.push(points[i].x, points[i].y, points[i].z, points[i + 1].x, points[i + 1].y, points[i + 1].z)
      }
    }
    for (let longitude = 0; longitude < 180; longitude += 30) {
      const arc: LatLon[] = []
      for (let latitude = -90; latitude <= 90; latitude += 6) arc.push({ latitude, longitude })
      rule(arc.map((p) => spherePoint(p, 1.001)))
      const opposite: LatLon[] = []
      for (let latitude = -90; latitude <= 90; latitude += 6) opposite.push({ latitude, longitude: longitude + 180 })
      rule(opposite.map((p) => spherePoint(p, 1.001)))
    }
    for (let latitude = -60; latitude <= 60; latitude += 30) {
      const parallel: LatLon[] = []
      for (let longitude = 0; longitude <= 360; longitude += 6) parallel.push({ latitude, longitude })
      rule(parallel.map((p) => spherePoint(p, 1.001)))
    }
    const graticuleGeometry = new THREE.BufferGeometry()
    graticuleGeometry.setAttribute('position', new THREE.Float32BufferAttribute(graticule, 3))
    const graticuleMaterial = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.13, depthWrite: false })
    spin.add(new THREE.LineSegments(graticuleGeometry, graticuleMaterial))

    // One beacon per catalogue location that carries coordinates.
    const beaconCount = Math.max(stations.length, 1)
    const beaconPosition = new Float32Array(beaconCount * 3)
    const beaconSize = new Float32Array(beaconCount)
    const beaconPhase = new Float32Array(beaconCount)
    const beaconShade = new Float32Array(beaconCount)
    stations.forEach((station, index) => {
      const p = spherePoint(station, 1.012)
      beaconPosition[index * 3] = p.x
      beaconPosition[index * 3 + 1] = p.y
      beaconPosition[index * 3 + 2] = p.z
      beaconSize[index] = 8
      beaconPhase[index] = Math.random()
      beaconShade[index] = 1
    })
    const beaconGeometry = new THREE.BufferGeometry()
    beaconGeometry.setAttribute('position', new THREE.BufferAttribute(beaconPosition, 3))
    beaconGeometry.setAttribute('aSize', new THREE.BufferAttribute(beaconSize, 1))
    beaconGeometry.setAttribute('aPhase', new THREE.BufferAttribute(beaconPhase, 1))
    beaconGeometry.setAttribute('aShade', new THREE.BufferAttribute(beaconShade, 1))
    const beaconMaterial = new THREE.ShaderMaterial({
      uniforms: { ...uniforms, uColor: { value: new THREE.Color('#4e8aa8') }, uOpacity: { value: 0.92 } },
      vertexShader: POINT_VERTEX,
      fragmentShader: BEACON_FRAGMENT,
      transparent: true,
      depthWrite: false,
    })
    const beacons = new THREE.Points(beaconGeometry, beaconMaterial)
    if (stations.length) spin.add(beacons)

    // Atmosphere shell, rendered back-facing so only the rim survives. It has to sit close to
    // the body or the gap between the two becomes visible.
    const halo = new THREE.Mesh(
      new THREE.SphereGeometry(1.085, 48, 32),
      new THREE.ShaderMaterial({
        uniforms: { uColor: { value: new THREE.Color('#b15a2e') }, uStrength: { value: 0.3 } },
        vertexShader: HALO_VERTEX,
        fragmentShader: HALO_FRAGMENT,
        side: THREE.BackSide,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    )
    root.add(halo)

    // Satellite-track ring for records that came from a satellite source.
    const orbit = new THREE.Group()
    orbit.rotation.set(0.42, 0, 0.62)
    const trackRadius = 1.32
    const track: number[] = []
    for (let i = 0; i <= 128; i += 1) {
      const a = (i / 128) * Math.PI * 2
      track.push(Math.cos(a) * trackRadius, Math.sin(a) * trackRadius, 0)
    }
    const trackGeometry = new THREE.BufferGeometry()
    trackGeometry.setAttribute('position', new THREE.Float32BufferAttribute(track, 3))
    const trackMaterial = new THREE.LineBasicMaterial({ transparent: true, opacity: 0.16, depthWrite: false })
    orbit.add(new THREE.Line(trackGeometry, trackMaterial))
    const craftMaterial = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.9 })
    const craft = new THREE.Mesh(new THREE.SphereGeometry(0.019, 10, 10), craftMaterial)
    orbit.add(craft)
    root.add(orbit)

    const palette = {
      field: fieldMaterial.uniforms.uColor.value as THREE.Color,
      beacon: beaconMaterial.uniforms.uColor.value as THREE.Color,
      halo: (halo.material as THREE.ShaderMaterial).uniforms.uColor.value as THREE.Color,
      grid: graticuleMaterial.color,
      craft: craftMaterial.color,
      top: bodyUniforms.uTop.value as THREE.Color,
      bottom: bodyUniforms.uBottom.value as THREE.Color,
      limb: bodyUniforms.uLimb.value as THREE.Color,
    }

    const haloMaterial = halo.material as THREE.ShaderMaterial

    const applyColors = () => {
      const dark = document.documentElement.classList.contains('dark')
      palette.field.copy(cssColor(host, '--brand', '#b15a2e'))
      palette.beacon.copy(cssColor(host, '--info-strong', '#4e8aa8'))
      palette.halo.copy(cssColor(host, '--brand', '#b15a2e'))
      palette.grid.copy(cssColor(host, '--text', '#1c2624'))
      palette.craft.copy(cssColor(host, '--brand', '#b15a2e'))
      palette.top.copy(cssColor(host, '--globe-lit', '#fffaf1'))
      palette.bottom.copy(cssColor(host, '--globe-shade', '#ded0b6'))
      palette.limb.copy(cssColor(host, '--globe-limb', '#a8946f'))
      // Additive blending gives the dark theme its atmosphere but clips to pink on white,
      // so the light theme uses plain alpha for the same rim.
      haloMaterial.blending = dark ? THREE.AdditiveBlending : THREE.NormalBlending
      haloMaterial.uniforms.uStrength.value = dark ? 0.2 : 0.17
      haloMaterial.needsUpdate = true
      graticuleMaterial.opacity = dark ? 0.14 : 0.2
      trackMaterial.opacity = dark ? 0.16 : 0.22
    }
    applyColors()
    applyColorsRef.current = applyColors

    const raycaster = new THREE.Raycaster()
    raycaster.params.Points.threshold = 0.045
    const pointerNdc = new THREE.Vector2()
    let parallax = { x: 0, y: 0 }
    let eased = { x: 0, y: 0 }
    let hovered = -1

    const onPointerMove = (event: PointerEvent) => {
      const box = host.getBoundingClientRect()
      if (!box.width || !box.height) return
      const nx = (event.clientX - box.left) / box.width
      const ny = (event.clientY - box.top) / box.height
      pointerNdc.set(nx * 2 - 1, -(ny * 2 - 1))
      parallax = { x: nx * 2 - 1, y: ny * 2 - 1 }
      if (!stations.length) return
      raycaster.setFromCamera(pointerNdc, camera)
      const hit = raycaster.intersectObject(beacons, false)[0]
      hovered = hit && typeof hit.index === 'number' ? hit.index : -1
      const readout = readoutRef.current
      if (!readout) return
      if (hovered === -1) {
        readout.style.opacity = '0'
        host.style.cursor = ''
        return
      }
      const station = stations[hovered]
      const world = new THREE.Vector3(beaconPosition[hovered * 3], beaconPosition[hovered * 3 + 1], beaconPosition[hovered * 3 + 2])
      beacons.localToWorld(world)
      const screen = world.project(camera)
      readout.textContent = `${station.name} · ${station.region}`
      readout.style.transform = `translate(${((screen.x + 1) / 2) * box.width}px, ${((-screen.y + 1) / 2) * box.height}px)`
      readout.style.opacity = '1'
      host.style.cursor = 'crosshair'
    }
    const onPointerLeave = () => {
      parallax = { x: 0, y: 0 }
      hovered = -1
      if (readoutRef.current) readoutRef.current.style.opacity = '0'
      host.style.cursor = ''
    }
    renderer.domElement.addEventListener('pointermove', onPointerMove)
    renderer.domElement.addEventListener('pointerleave', onPointerLeave)

    let width = host.clientWidth || 1
    let height = host.clientHeight || 1
    const resize = () => {
      width = host.clientWidth || width
      height = host.clientHeight || height
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      if (reduced) renderer.render(scene, camera)
    }
    resize()
    const sizeObserver = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(resize) : null
    sizeObserver?.observe(host)

    let onScreen = true
    let screenObserver: IntersectionObserver | null = null
    if (typeof IntersectionObserver !== 'undefined') {
      screenObserver = new IntersectionObserver((entries) => {
        onScreen = entries.some((entry) => entry.isIntersecting)
      })
      screenObserver.observe(host)
    }

    const clock = new THREE.Clock()
    const startedAt = performance.now()
    let frame = 0

    /** Reduced motion gets one settled frame: the same scene, frozen at its focus. */
    if (reduced) {
      uniforms.uEntrance.value = 1
      renderer.render(scene, camera)
    } else {
      const render = () => {
        frame = requestAnimationFrame(render)
        if (!onScreen || document.hidden) return
        const delta = Math.min(clock.getDelta(), 0.05)
        const elapsed = (performance.now() - startedAt) / 1000
        uniforms.uTime.value = elapsed
        uniforms.uEntrance.value = Math.min(1, easeOutCubic(Math.min(1, elapsed / 0.85)))
        root.scale.setScalar(0.9 + 0.1 * uniforms.uEntrance.value)
        spin.rotation.y = focusLonRad + Math.sin(elapsed * DRIFT_RATE) * DRIFT_AMPLITUDE
        tilt.rotation.x = focusLatRad + Math.sin(elapsed * DRIFT_RATE * 0.7) * 0.05
        orbit.rotation.y += delta * 0.05
        const angle = elapsed * 0.32
        craft.position.set(Math.cos(angle) * trackRadius, Math.sin(angle) * trackRadius, 0)
        eased = {
          x: eased.x + (parallax.x * 0.34 - eased.x) * Math.min(1, delta * 3.4),
          y: eased.y + (-parallax.y * 0.24 - eased.y) * Math.min(1, delta * 3.4),
        }
        camera.position.x = eased.x
        camera.position.y = eased.y
        camera.lookAt(0, 0, 0)
        renderer.render(scene, camera)
      }
      frame = requestAnimationFrame(render)
    }

    return () => {
      cancelAnimationFrame(frame)
      applyColorsRef.current = null
      sizeObserver?.disconnect()
      screenObserver?.disconnect()
      renderer.domElement.removeEventListener('pointermove', onPointerMove)
      renderer.domElement.removeEventListener('pointerleave', onPointerLeave)
      scene.traverse((object) => {
        const mesh = object as THREE.Mesh
        mesh.geometry?.dispose()
        const material = mesh.material as THREE.Material | THREE.Material[] | undefined
        if (Array.isArray(material)) material.forEach((entry) => entry.dispose())
        else if (material) material.dispose()
      })
      renderer.dispose()
      if (renderer.domElement.parentNode === host) host.removeChild(renderer.domElement)
    }
  }, [stations, focus.latitude, focus.longitude])

  return (
    <div ref={hostRef} className={className} data-globe-canvas="true" aria-hidden="true">
      <div
        ref={readoutRef}
        className="pointer-events-none absolute left-0 top-0 z-10 -translate-y-9 whitespace-nowrap rounded-md border px-2 py-1 font-mono text-[11px] transition-opacity duration-150"
        style={{ borderColor: 'var(--border-strong)', backgroundColor: 'var(--surface)', color: 'var(--text)', opacity: 0 }}
      />
    </div>
  )
}
