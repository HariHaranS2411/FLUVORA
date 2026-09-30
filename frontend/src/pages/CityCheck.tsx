import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import type { LocateResult, ModelInfo } from '../types'
import { Card, NA, RiskBadge } from '../components/Bits'
import PlainStory from '../components/PlainStory'
import { fmtDelta, levelFor, probHeadline, type ModelThresholds } from '../utils/plain'

export default function CityCheck() {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [res, setRes] = useState<LocateResult | null>(null)
  const [showDetail, setShowDetail] = useState(false)
  const [thresholds, setThresholds] = useState<ModelThresholds | null>(null)

  useEffect(() => {
    api.modelInfo()
      .then((m: ModelInfo) => setThresholds(m.thresholds))
      .catch(() => setThresholds(null))
  }, [])

  const check = async (name?: string) => {
    const query = (name ?? q).trim()
    if (!query) return
    setBusy(true)
    setErr(null)
    setRes(null)
    try {
      setRes(await api.locate(query))
    } catch (e) {
      setErr(String((e as Error).message || e))
    } finally {
      setBusy(false)
    }
  }

  const e = res?.explanation

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold">Check My City</h1>
        <p className="text-sm text-night-400">
          Type any Indian city or town. We resolve it to its district using a real geocoder,
          then show the district's current flash-flood risk — in everyday words first, with the
          exact numbers below for anyone who wants them.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <input
          className="border rounded-md px-4 py-2.5 text-sm flex-1 min-w-[240px]"
          placeholder="e.g. Coimbatore, Guwahati, Nashik, Patna…"
          value={q}
          onChange={(ev) => setQ(ev.target.value)}
          onKeyDown={(ev) => ev.key === 'Enter' && check()}
        />
        <button
          className="bg-sky-700 text-white rounded-md px-5 py-2.5 text-sm hover:bg-sky-500 disabled:opacity-50"
          onClick={() => check()} disabled={busy || !q.trim()}
        >
          {busy ? 'Checking…' : 'Check risk'}
        </button>
      </div>

      {err && (
        <div className="bg-panel-red border border-red-800/60 text-red-300 rounded-lg p-3 text-sm">
          Lookup failed: {err}
        </div>
      )}

      {res && !res.found && (
        <div className="bg-panel-amber border border-amber-700/60 text-amber-200 rounded-lg p-4 text-sm">
          ⚠️ {res.detail ?? 'Not found.'} Try a nearby larger city.
        </div>
      )}

      {res?.found && e && (
        <div className="space-y-4">
          <div className="bg-night-800 rounded-xl border border-night-600 shadow-sm p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-bold">{res.city!.name}</h2>
                <p className="text-sm text-night-400">
                  {res.city!.state} · resolved to district{' '}
                  <Link className="text-sky-400 hover:underline" to={`/location/${res.district!.id}`}>
                    {res.district!.name}
                  </Link>
                  {res.resolution?.method === 'nearest-centroid' &&
                    ` (city centre is ${res.resolution.distance_km} km from the district polygon — nearest match)`}
                </p>
              </div>
              <div className="text-right">
                <RiskBadge level={levelFor(e.probability, thresholds)} />
                {e.probability != null && (
                  <p className="text-sm text-night-300 mt-1">{probHeadline(e.probability)}</p>
                )}
                {e.prediction_horizon && (
                  <p className="text-xs text-night-400 mt-0.5">Horizon: {e.prediction_horizon}</p>
                )}
              </div>
            </div>
            <p className="text-xs text-night-400 mt-2">
              ⚠️ Risk is assessed at <b>district level</b> from district-average data. City-level
              or neighborhood-level hydrological predictions are not currently available.
            </p>
            {e.data_cut && (
              <p className="text-xs text-night-500 mt-2">
                Based on observations up to {new Date(e.data_cut).toLocaleString()} ·{' '}
                {Object.entries(e.freshness).map(([k, v]) => `${k.replace('_', ' ')}: ${v}`).join(' · ')}
              </p>
            )}
          </div>

          {e.missing_critical ? (
            <div className="bg-panel-amber border border-amber-700/60 text-amber-200 rounded-lg p-4 text-sm">
              ⚠️ Some core measurements (rain, soil or river) are missing for this district right
              now, so no reliable risk estimate can be produced. Nothing is invented — this page
              will show real numbers again as soon as the data returns.
            </div>
          ) : (
            <PlainStory ex={e} thresholds={thresholds} title="What this means for you" />
          )}

          <Card title="Why this level? (from the model, using real data)">
            <ul className="text-sm space-y-1">
              {e.contributions.length === 0 && (
                <li className="text-night-400">
                  No indicator currently moves the probability materially.
                </li>
              )}
              {e.contributions.slice(0, 6).map((c) => {
                const f = e.features.find((x) => x.feature === c.feature)
                return (
                  <li key={c.feature}>
                    {c.delta > 0 ? '▲' : '▼'} <b>{f?.label ?? c.feature}</b>{' '}
                    {c.delta > 0 ? 'raised' : 'lowered'} the estimate by{' '}
                    <b>{fmtDelta(c.delta)}</b>
                    {f?.value != null && f.hist_p90 != null && (
                      <span className="text-night-400">
                        {' '}(now {f.value.toFixed(f.unit === 'ratio' ? 2 : 1)}
                        {f.unit ? ` ${f.unit}` : ''} vs historical 90th pct {f.hist_p90.toFixed(2)})
                      </span>
                    )}
                  </li>
                )
              })}
            </ul>
            <p className="text-xs text-night-400 mt-2">{e.method_notes.contributions}</p>
          </Card>

          {e.last_flood && (
            <Card title={`Conditions during the last flood here (${e.last_flood.days_ago} days ago)`}>
              <p className="text-sm mb-2">
                {new Date(e.last_flood.date).toLocaleDateString()} — {e.last_flood.kind}
                {e.last_flood.event_name ? `: ${e.last_flood.event_name}` : ''}
                {e.last_flood.glide ? ` (GLIDE ${e.last_flood.glide})` : ''}
              </p>
              <div className="overflow-x-auto">
                <table className="min-w-full text-xs">
                  <thead className="bg-night-700 text-left uppercase text-night-400">
                    <tr>
                      <th className="px-2 py-1">Condition</th>
                      <th className="px-2 py-1">During that flood</th>
                      <th className="px-2 py-1">Now</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[['Max daily rainfall (mm)', 'max_daily_rain_mm'],
                      ['Mean soil moisture (m³/m³)', 'mean_soil_moisture'],
                      ['Max river discharge (m³/s)', 'max_discharge_m3s']].map(([label, k]) => (
                      <tr key={k} className="border-t border-night-600">
                        <td className="px-2 py-1">{label}</td>
                        <td className="px-2 py-1">{fmtVal(e.last_flood!.conditions_during_flood[k as keyof typeof e.last_flood.conditions_during_flood])}</td>
                        <td className="px-2 py-1 font-semibold">{fmtVal(e.last_flood!.conditions_now[k as keyof typeof e.last_flood.conditions_now])}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-night-400 mt-2">
                The model's flood-memory features (flood-level river days in the past 30/90
                days, days since the last flood-level day, reported flood days) come from this
                real history, so a recent flood keeps the baseline probability elevated.
              </p>
            </Card>
          )}

          <Card title="For experts: the exact numbers">
            <button className="text-sm text-sky-400 hover:underline mb-2"
                    onClick={() => setShowDetail((v) => !v)}>
              {showDetail ? 'Hide' : 'Show'} the detailed feature table
            </button>
            {showDetail && (
              <>
                <ol className="text-sm space-y-1 text-night-300 list-decimal list-inside mb-3">
                  <li>Latest real observations for this district (rain, soil moisture, river discharge) are fetched.</li>
                  <li>Features identical to training are computed (rainfall windows, saturation, discharge ratios, terrain, season).</li>
                  <li>The trained model ({e.model_version}) outputs a probability from patterns learned on 2020–2023 history.</li>
                  <li>Documented validation-derived thresholds map the probability to LOW / MODERATE / HIGH / CRITICAL.</li>
                </ol>
                <div className="overflow-x-auto">
                  <table className="min-w-full text-xs">
                    <thead className="bg-night-700 text-left uppercase text-night-400">
                      <tr>
                        <th className="px-2 py-1">Feature</th>
                        <th className="px-2 py-1">Value now</th>
                        <th className="px-2 py-1">Hist. median</th>
                        <th className="px-2 py-1">90th pct</th>
                        <th className="px-2 py-1">99th pct</th>
                      </tr>
                    </thead>
                    <tbody>
                      {e.features.filter((f) => f.value != null).map((f) => (
                        <tr key={f.feature} className="border-t border-night-600">
                          <td className="px-2 py-1">{f.label}{f.unit ? ` (${f.unit})` : ''}</td>
                          <td className="px-2 py-1 font-semibold">
                            {f.value == null ? <NA /> : f.value.toFixed(f.unit === 'ratio' ? 2 : 1)}
                          </td>
                          <td className="px-2 py-1">{f.hist_median == null ? <NA /> : f.hist_median.toFixed(2)}</td>
                          <td className="px-2 py-1">{f.hist_p90 == null ? <NA /> : f.hist_p90.toFixed(2)}</td>
                          <td className="px-2 py-1">{f.hist_p99 == null ? <NA /> : f.hist_p99.toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </Card>

          <p className="text-xs text-night-400">
            Probabilistic decision support — not a guarantee of flooding or safety. Follow
            official warnings from IMD, CWC, NDMA and local authorities.{' '}
            <Link className="text-sky-400 hover:underline" to={`/location/${res.district!.id}`}>
              Full district page →
            </Link>
          </p>
        </div>
      )}

      {!res && !err && !busy && (
        <Card title="Try one of these">
          <div className="flex flex-wrap gap-2">
            {['Mumbai', 'Delhi', 'Bengaluru', 'Chennai', 'Guwahati', 'Coimbatore'].map((c) => (
              <button key={c} className="border rounded-full px-3 py-1.5 text-sm hover:bg-sky-900/40"
                      onClick={() => { setQ(c); check(c) }}>
                {c}
              </button>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

function fmtVal(v: number | null): string {
  return v == null ? 'N/A' : String(v)
}
