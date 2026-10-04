import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { MapContainer, GeoJSON, TileLayer, Marker, useMapEvents } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { RiskMap } from '../types'
import { RISK_COLORS } from '../types'
import { API_BASE } from '../services/api'

const INDIA_BOUNDS: [[number, number], [number, number]] = [[6.5, 68], [36.5, 97.5]]

type Props = {
  riskMap: RiskMap | null
  height?: number
  showStates?: boolean
  /** boundary level shown on the map: states (ADM1) or districts (ADM2) */
  level?: 'state' | 'district'
  onLevelChange?: (l: 'state' | 'district') => void
}

function ZoomAware({ onZoom }: { onZoom: (z: number) => void }) {
  useMapEvents({ zoomend: (e) => onZoom(e.target.getZoom()) })
  return null
}

/** Legend explaining exactly what is being shown, in plain language. */
function MapLegend({ showStates, level }: { showStates: boolean; level: 'state' | 'district' }) {
  return (
    <div className="absolute bottom-3 left-3 z-[600] bg-night-950/95 rounded-lg shadow-lg px-3 py-2.5 text-[11px] leading-relaxed max-w-[240px] border border-night-600">
      <div className="font-semibold text-night-50 mb-1">
        Model-estimated high-flow risk by {level === 'state' ? 'state' : 'district'} — next 0–24 h
      </div>
      <div className="space-y-1">
        {Object.entries(RISK_COLORS).map(([k, c]) => (
          <div key={k} className="flex items-center gap-2">
            <span className="inline-block w-4 h-4 rounded-sm border border-night-500" style={{ background: c }} />
            <span className="text-night-200">
              {k === 'DATA_UNAVAILABLE' ? 'No data — assessment unavailable' : k.replace('_', ' ')}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-2 pt-2 border-t border-night-600 text-night-400">
        Hydrological threshold exceedance estimate (GloFAS-based) — not an official flood
        warning.
        {level === 'state'
          ? ' States are colored by their most at-risk district; click for detail. Labels show each state\'s name.'
          : ' Click any district for its full prediction, data and explanation.'}
        {showStates && level === 'district' ? ' Bright white lines = State/UT borders.' : ''}
      </div>
    </div>
  )
}

type HoverInfo = {
  name: string
  level: string
  p: number | null
  detail?: string
}

type StateLabel = { name: string; lat: number; lng: number; area: number }

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

/**
 * Centroid + rough area (deg²) of each state polygon's largest ring,
 * used to place state-name labels. Area-centroid (shoelace) keeps the
 * label inside concave shapes better than a vertex average.
 */
function stateLabels(fc: GeoJSON.FeatureCollection): StateLabel[] {
  const out: StateLabel[] = []
  for (const f of fc.features) {
    const g = f.geometry
    if (!g) continue
    const rings: [number, number][][] =
      g.type === 'Polygon' ? [g.coordinates[0] as [number, number][]]
        : g.type === 'MultiPolygon' ? (g.coordinates as [number, number][][][]).map((p) => p[0] as [number, number][])
          : []
    let best: { a: number; c: [number, number] } | null = null
    for (const ring of rings) {
      let a = 0, cx = 0, cy = 0
      for (let i = 0; i < ring.length - 1; i++) {
        const [x0, y0] = ring[i]
        const [x1, y1] = ring[i + 1]
        const cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
      }
      a /= 2
      if (Math.abs(a) < 1e-12) continue
      const c: [number, number] = [cx / (6 * a), cy / (6 * a)]
      if (!best || Math.abs(a) > Math.abs(best.a)) best = { a, c }
    }
    if (best) {
      const name = (f.properties as { shapeName?: string } | undefined)?.shapeName ?? ''
      if (name) out.push({ name, lat: best.c[1], lng: best.c[0], area: Math.abs(best.a) })
    }
  }
  return out
}

export default function IndiaRiskMap({ riskMap, height = 560, showStates = true, level = 'district', onLevelChange }: Props) {
  const nav = useNavigate()
  const [geo, setGeo] = useState<GeoJSON.FeatureCollection | null>(null)
  const [stateBorders, setStateBorders] = useState<GeoJSON.FeatureCollection | null>(null)
  const [zoom, setZoom] = useState(5)
  const [hover, setHover] = useState<HoverInfo | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setGeo(null) // clear old geometry immediately so the level switch is visible
    fetch(`${API_BASE}/api/map-data?level=${level}`)
      .then(async (r) => {
        if (!r.ok) throw new Error(`map geometry/risk unavailable (${r.status})`)
        const data = await r.json()
        if (!cancelled) setGeo(data)
      })
      .catch((e) => { if (!cancelled) setErr(String(e.message || e)) })
    return () => { cancelled = true }
  }, [level])

  // State borders highlight on EVERY map (district mode overlays them on top of
  // districts; state mode keeps them visible even though polygons are states).
  useEffect(() => {
    fetch(`${API_BASE}/api/geo/states`)
      .then(async (r) => (r.ok ? r.json() : null))
      .then((d) => d && setStateBorders(d))
      .catch(() => {})
  }, [])

  const styleFeature = (feature?: GeoJSON.Feature) => {
    const p = (feature?.properties ?? {}) as {
      district_id?: number; risk_level?: string
    }
    const lvl = p.risk_level ?? 'DATA_UNAVAILABLE'
    const emphasized = lvl === 'HIGH' || lvl === 'CRITICAL'
    return {
      fillColor: RISK_COLORS[lvl] ?? '#64748b',
      fillOpacity: level === 'state' ? 0.85 : zoom >= 7 ? 0.6 : 0.88,
      color: emphasized ? '#fbbf24' : level === 'state' ? '#0b1220' : '#334155',
      weight: level === 'state' ? 0.8 : zoom >= 7 ? 1.2 : emphasized ? 1.2 : 0.35,
    }
  }

  // Dark casing under the white stroke: makes the highlight read stronger and
  // gives the borders dark definition against both fills and basemap tiles.
  const styleStateBorderCasing = () => ({
    color: '#020617',
    weight: 6.5,
    fill: false,
    interactive: false,
    opacity: 0.9,
    lineJoin: 'round' as const,
    lineCap: 'round' as const,
  })

  const styleStateBorders = () => ({
    color: '#f8fafc',
    weight: 3,
    fill: false,
    interactive: false,
    opacity: 1,
    lineJoin: 'round' as const,
  })

  // State-name labels: large states always, small ones only when zoomed in.
  const stateLabelList = useMemo(
    () => (level === 'state' && geo ? stateLabels(geo).filter((l) => l.area >= 0.25 || zoom >= 6) : []),
    [geo, level, zoom],
  )

  if (err) {
    return (
      <div className="bg-panel-amber border border-amber-700/60 text-amber-200 rounded-lg p-4 text-sm">
        ⚠️ Map unavailable: {err}
      </div>
    )
  }

  return (
    <div key={level} className="relative rounded-xl overflow-hidden border border-night-500 shadow-sm" style={{ height }}>
      <MapContainer
        bounds={INDIA_BOUNDS}
        maxBounds={INDIA_BOUNDS}
        maxBoundsViscosity={0.8}
        minZoom={4}
        maxZoom={10}
        scrollWheelZoom
        style={{ height: '100%', background: '#0a1120' }}
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <ZoomAware onZoom={setZoom} />
        {geo && (
          <GeoJSON
            key={`${level}-${riskMap?.computed_at ?? 'init'}`}
            data={geo}
            style={styleFeature}
            onEachFeature={(feature, layer) => {
              const p = feature.properties as {
                shapeName: string; district_id?: number; risk_level: string;
                probability?: number | null; max_probability?: number | null;
                worst_district?: string | null; districts_total?: number;
                risk_counts?: Record<string, number> | null
              }
              const isState = level === 'state'
              layer.on({
                mouseover: () => setHover(isState
                  ? {
                      name: p.shapeName,
                      level: p.risk_level ?? 'DATA_UNAVAILABLE',
                      p: p.max_probability ?? null,
                      detail: p.districts_total
                        ? `${p.districts_total} districts`
                          + (p.worst_district ? ` · highest: ${p.worst_district}` : '')
                        : 'no district data',
                    }
                  : {
                      name: p.shapeName,
                      level: p.risk_level ?? 'DATA_UNAVAILABLE',
                      p: p.probability ?? null,
                    }),
                mouseout: () => setHover(null),
                click: () => {
                  if (!isState && p.district_id) nav(`/location/${p.district_id}`)
                },
              })
            }}
          />
        )}
        {stateBorders && showStates && (
          <>
            <GeoJSON key="state-borders-casing" data={stateBorders} style={styleStateBorderCasing} />
            <GeoJSON key="state-borders" data={stateBorders} style={styleStateBorders} />
          </>
        )}
        {stateLabelList.map((l) => (
          <Marker
            key={`state-label-${l.name}`}
            position={[l.lat, l.lng]}
            interactive={false}
            icon={L.divIcon({
              className: 'state-label-icon',
              html: `<span>${esc(l.name)}</span>`,
              iconSize: [0, 0],
            })}
          />
        ))}
      </MapContainer>

      {/* Hover readout — plain-language, top-right (below the level toggle) */}
      <div className="absolute top-12 right-3 z-[600] pointer-events-none">
        {hover ? (
          <div className="bg-night-950/95 border border-night-600 rounded-lg shadow-lg px-3 py-2 text-xs">
            <div className="font-semibold text-night-50">{hover.name}</div>
            <div className="flex items-center gap-1.5 mt-0.5">
              <span className="inline-block w-3 h-3 rounded-sm" style={{ background: RISK_COLORS[hover.level] ?? '#64748b' }} />
              <span className="text-night-200">
                {hover.level.replace('_', ' ')}
                {hover.p != null ? ` · ${Math.round(hover.p * 100)}% model estimate` : ''}
              </span>
            </div>
            {hover.detail && <div className="text-night-300 mt-0.5">{hover.detail}</div>}
            <div className="text-night-400 mt-0.5">
              {level === 'state' ? 'Switch to districts for detail' : 'Click for full details'}
            </div>
          </div>
        ) : (
          <div className="bg-night-950/85 border border-night-600 rounded-lg shadow px-3 py-2 text-xs text-night-400">
            Hover a {level === 'state' ? 'state' : 'district'} · click for its prediction
          </div>
        )}
      </div>

      {onLevelChange && (
        <div className="absolute top-3 right-3 z-[600] flex rounded-lg overflow-hidden border border-night-500 text-[11px]">
          {(['state', 'district'] as const).map((l) => (
            <button
              key={l}
              className={`px-3 py-1.5 capitalize ${level === l ? 'bg-sky-600 text-white' : 'bg-night-950/90 text-night-300 hover:bg-night-700'}`}
              onClick={() => onLevelChange(l)}
            >
              {l}
            </button>
          ))
          }
        </div>
      )}

      <MapLegend showStates={showStates} level={level} />
      {!geo && !err && (
        <div className="absolute inset-0 z-[600] flex items-center justify-center bg-night-900/80 text-sm text-night-300">
          Loading real {level === 'state' ? 'state' : 'district'} geometry and risk…
        </div>
      )}
    </div>
  )
}
