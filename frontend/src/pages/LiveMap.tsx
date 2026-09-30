import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../services/api'
import type { District, RiskMap } from '../types'
import IndiaRiskMap from '../maps/IndiaRiskMap'
import { RiskBadge } from '../components/Bits'
import { normName } from '../utils/text'

export default function LiveMap() {
  const [risk, setRisk] = useState<RiskMap | null>(null)
  const [states, setStates] = useState<{ state: string; districts: number }[]>([])
  const [state, setState] = useState('')
  const [districts, setDistricts] = useState<District[]>([])
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(false)
  const [level, setLevel] = useState<'state' | 'district'>('district')
  const nav = useNavigate()

  useEffect(() => {
    api.riskMap().then(setRisk).catch(() => {})
    api.states().then(setStates).catch(() => {})
  }, [])

  useEffect(() => {
    if (state) api.districts(state).then(setDistricts).catch(() => {})
    else setDistricts([])
  }, [state])

  const filtered = useMemo(
    () => districts.filter((d) => normName(d.name).includes(normName(q))).slice(0, 8),
    [districts, q],
  )

  const counts = risk?.counts
  const top = useMemo(
    () => (risk?.districts ?? [])
      .filter((d) => d.risk_level === 'HIGH' || d.risk_level === 'CRITICAL')
      .sort((a, b) => (b.probability ?? 0) - (a.probability ?? 0))
      .slice(0, 6),
    [risk],
  )

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Live Risk Map</h1>
        <p className="text-sm text-night-400">
          Every {level === 'state' ? 'state is colored by its most at-risk district' : 'district is colored by its current model-estimated probability of high-flow (threshold exceedance) conditions'} — a hydrological estimate computed from real
          rainfall, soil-moisture and river-discharge data for the next 0–24 h. Gray = insufficient
          data (never “low risk”).{level === 'district' ? ' Click a district to see exactly how its number was calculated.' : ''}
        </p>
      </div>

      <div className="flex flex-wrap gap-2 items-center relative">
        <select
          className="border rounded-md px-3 py-2 text-sm bg-night-800"
          value={state}
          onChange={(e) => setState(e.target.value)}
        >
          <option value="">All India</option>
          {states.map((s) => (
            <option key={s.state} value={s.state}>{s.state} ({s.districts})</option>
          ))}
        </select>
        <input
          className="border rounded-md px-3 py-2 text-sm w-64"
          placeholder="Search district…"
          value={q}
          onFocus={() => setOpen(true)}
          onChange={(e) => { setQ(e.target.value); setOpen(true) }}
        />
        {open && q && filtered.length > 0 && (
          <div className="absolute z-[1000] top-11 left-44 bg-night-800 border rounded-lg shadow-xl w-80">
            {filtered.map((d) => {
              const r = risk?.districts.find((x) => x.district_id === d.id)
              return (
                <button
                  key={d.id}
                  className="block w-full text-left px-3 py-2 hover:bg-sky-900/40 text-sm flex items-center justify-between"
                  onClick={() => { setOpen(false); nav(`/location/${d.id}`) }}
                >
                  <span>{d.name}, {d.state}</span>
                  {r && <RiskBadge level={r.risk_level} />}
                </button>
              )
            })}
          </div>
        )}
      </div>

      <IndiaRiskMap riskMap={risk} height={600} level={level} onLevelChange={setLevel} />

      {counts && (
        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-night-300">
          <span><b className="text-red-400">{counts.critical}</b> critical</span>
          <span><b className="text-orange-400">{counts.high}</b> high</span>
          <span><b className="text-yellow-400">{counts.moderate}</b> moderate</span>
          <span><b className="text-green-400">{counts.low}</b> low (no signals)</span>
          <span><b className="text-night-400">{counts.data_unavailable}</b> no data — assessment unavailable (distinct from low)</span>
        </div>
      )}

      {top.length > 0 && (
        <div className="bg-night-800 rounded-xl border border-night-600 shadow-sm p-4">
          <h2 className="text-sm font-semibold text-night-400 uppercase tracking-wide mb-2">
            Highest-risk districts right now
          </h2>
          <div className="flex flex-wrap gap-2">
            {top.map((d) => (
              <button
                key={d.district_id}
                onClick={() => nav(`/location/${d.district_id}`)}
                className="border rounded-lg px-3 py-2 text-sm hover:bg-night-900 text-left"
              >
                <div className="font-medium">{d.name}</div>
                <div className="text-xs text-night-400">{d.state}</div>
                <div className="mt-1"><RiskBadge level={d.risk_level} /></div>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
