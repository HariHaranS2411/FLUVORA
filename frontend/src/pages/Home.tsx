import { Suspense, lazy, useEffect, useState } from 'react'
import { api, API_BASE } from '../services/api'
import type { RiskMap } from '../types'
import { NA } from '../components/Bits'

// Leaflet + geojson handling load after first paint — Home's header, stats
// and source panel render without waiting for the ~400 kB map chunk.
const IndiaRiskMap = lazy(() => import('../maps/IndiaRiskMap'))
const MapFallback = () => (
  <div className="h-[560px] rounded-xl border border-night-600 bg-night-800/60 flex items-center justify-center">
    <span className="inline-block h-6 w-6 animate-spin rounded-full border-2 border-sky-500 border-t-transparent" aria-hidden />
  </div>
)

export default function Home() {
  const [risk, setRisk] = useState<RiskMap | null>(null)
  const [sources, setSources] = useState<{ key: string; name: string; status: string; last_data_at: string | null }[]>([])
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    api.riskMap().then(setRisk).catch((e) => setErr(String(e.message || e)))
    fetch(`${API_BASE}/api/data-sources`)
      .then((r) => (r.ok ? r.json() : []))
      .then((rows: { key: string; name: string; status: string; last_data_at: string | null }[]) =>
        setSources(rows.filter((s) => !s.name.includes('NASA') && !s.name.includes('ReliefWeb')).slice(0, 6)))
      .catch(() => {})
  }, [])

  const c = risk?.counts
  return (
    <div className="space-y-6">
      <div className="bg-night-800 rounded-xl border border-night-600 shadow-sm p-5">
        <h1 className="text-2xl font-bold text-night-50">
          India Flash Flood Early Warning
          <span className="ml-2 align-middle rounded-md border border-sky-500/40 bg-sky-500/10 px-2 py-0.5 text-xs font-semibold tracking-[0.18em] text-sky-400">FLUVORA</span>
        </h1>
        <p className="text-sm text-night-300 mt-1">
          Today's hydrological risk outlook for {risk?.districts.length ?? '…'} Indian districts —
          a model estimate of high-flow threshold exceedance computed from real rainfall,
          soil-moisture, river and terrain data. Gray districts have insufficient data; they are
          never counted as “low risk”. Decision support only — not an official warning.
        </p>
        {err && <p className="mt-2 text-sm text-red-300">Risk data unavailable: {err}</p>}
        <div className="mt-4 grid grid-cols-2 sm:grid-cols-5 gap-3 text-center">
          {([
            ['Critical', c?.critical, '#dc2626', 'severe high-flow signals'],
            ['High', c?.high, '#ea580c', 'strong high-flow signals'],
            ['Moderate', c?.moderate, '#eab308', 'some high-flow signals'],
            ['Low', c?.low, '#16a34a', 'no high-flow signals'],
            ['No data', c?.data_unavailable, '#9ca3af', 'assessment unavailable'],
          ] as [string, number | undefined, string, string][]).map(([label, v, hex, sub], i) => (
            <div
              key={String(label)}
              className={`relative overflow-hidden rounded-lg border border-night-600 bg-night-700/40 p-3 transition-all duration-300 hover:border-night-500 hover:bg-night-700/70 chart-rise d${(i % 3) + 1}`}
            >
              <span
                aria-hidden
                className="absolute inset-x-0 top-0 h-0.5"
                style={{ background: `linear-gradient(90deg, transparent, ${hex}, transparent)` }}
              />
              <div className="text-2xl font-bold tabular-nums tracking-tight" style={{ color: hex, textShadow: `0 0 14px ${hex}40` }}>
                {v ?? 'N/A'}
              </div>
              <div className="text-xs font-medium text-night-200">{label}</div>
              <div className="text-[11px] text-night-500">{sub}</div>
            </div>
          ))}
        </div>
        {risk?.computed_at && (
          <p className="text-xs text-night-400 mt-3">
            Last risk computation: {new Date(risk.computed_at).toLocaleString()}
          </p>
        )}
      </div>

      <Suspense fallback={<MapFallback />}>
        <IndiaRiskMap riskMap={risk} />
      </Suspense>

      <div className="bg-night-800 rounded-xl border border-night-600 shadow-sm p-4">
        <h2 className="text-sm font-semibold text-night-400 uppercase tracking-wide mb-3">
          Latest data updates (live status)
        </h2>
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-2 text-sm">
          {sources.map((s) => (
            <div key={s.key} className="flex items-center justify-between border border-night-600 rounded-lg px-3 py-2">
              <span className="truncate">{s.name}</span>
              <span className="text-xs text-night-400 ml-2">
                {s.status === 'connected'
                  ? (s.last_data_at ? new Date(s.last_data_at).toLocaleString() : 'connected')
                  : s.status.replace('_', ' ')}
              </span>
            </div>
          ))}
          {sources.length === 0 && <NA>Source status not checked yet — open Data Sources to run a live check.</NA>}
        </div>
        <div className="mt-3 text-xs text-night-400">
          Source statuses come from live connection checks run by the backend (visible in the
          Alerts page footer of each alert and in the API at /api/data-sources).
        </div>
      </div>
    </div>
  )
}
