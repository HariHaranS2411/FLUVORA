import { Fragment, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ResponsiveContainer, BarChart, XAxis, YAxis, Bar, Cell } from 'recharts'
import { chartGradients, fancyTooltip, grad } from '../charts/Animated'
import { api } from '../services/api'
import type { Explanation, RiskMap } from '../types'
import { fmtProb, NA, RiskBadge } from '../components/Bits'
import { fmtDelta, levelVerdict, probHeadline, type RiskLevel } from '../utils/plain'
import { normName } from '../utils/text'
import { AXIS_TICK } from '../charts/Animated'
// recharts 2.x only renders real recharts elements or raw SVG as chart children,
// so the animated-chart kit exposes element factories (chartGradients() etc.).

export default function CurrentConditions() {
  const [risk, setRisk] = useState<RiskMap | null>(null)
  const [q, setQ] = useState('')
  const [openId, setOpenId] = useState<number | null>(null)
  const [explanation, setExplanation] = useState<Explanation | null>(null)
  const [loadingExp, setLoadingExp] = useState(false)
  const [expErr, setExpErr] = useState<string | null>(null)

  useEffect(() => {
    api.riskMap().then(setRisk).catch(() => {})
  }, [])

  const rows = useMemo(() => {
    const d = risk?.districts ?? []
    return d
      .filter((x) => `${normName(x.name)} ${normName(x.state)}`.includes(normName(q)))
      .sort((a, b) => (b.probability ?? -1) - (a.probability ?? -1))
  }, [risk, q])

  const toggle = async (id: number) => {
    if (openId === id) { setOpenId(null); setExplanation(null); return }
    setOpenId(id)
    setExplanation(null)
    setExpErr(null)
    setLoadingExp(true)
    try {
      setExplanation(await api.explain(id))
    } catch (e) {
      setExpErr(String((e as Error).message || e))
    } finally {
      setLoadingExp(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">Current Conditions</h1>
          <p className="text-sm text-night-400">
            Click any district row to see <b>why</b> it shows that probability — the exact
            feature values, historical percentiles and model contributions.
          </p>
        </div>
        <input
          className="border rounded-md px-3 py-2 text-sm"
          placeholder="Filter district or state…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </div>

      <div className="bg-night-800 rounded-xl border border-night-600 shadow-sm overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-night-700 text-left text-xs uppercase text-night-400">
            <tr>
              <th className="px-4 py-2">District</th>
              <th className="px-4 py-2">State</th>
              <th className="px-4 py-2">Risk</th>
              <th className="px-4 py-2">Probability</th>
              <th className="px-4 py-2">Trend</th>
              <th className="px-4 py-2">Confidence</th>
              <th className="px-4 py-2">Data cut</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 80).map((r) => (
              <Fragment key={r.district_id}>
                <tr
                    className={`border-t border-night-600 cursor-pointer ${openId === r.district_id ? 'bg-sky-900/40' : 'hover:bg-night-900'}`}
                    onClick={() => toggle(r.district_id)}>
                  <td className="px-4 py-2">
                    <span className="text-sky-400">{r.name}</span>
                    <span className="text-xs text-night-500 ml-2">{openId === r.district_id ? '▲' : '▼ why?'}</span>
                  </td>
                  <td className="px-4 py-2">{r.state}</td>
                  <td className="px-4 py-2"><RiskBadge level={r.risk_level} /></td>
                  <td className="px-4 py-2">{fmtProb(r.probability)}</td>
                  <td className="px-4 py-2">{r.trend}</td>
                  <td className="px-4 py-2">{r.confidence != null ? `${Math.round(r.confidence * 100)}%` : <NA />}</td>
                  <td className="px-4 py-2 text-xs">{r.data_cut ? new Date(r.data_cut).toLocaleDateString() : <NA />}</td>
                </tr>
                {openId === r.district_id && (
                  <tr className="bg-sky-900/30">
                    <td colSpan={7} className="px-6 py-4">
                      {loadingExp && <p className="text-sm text-night-400">Computing explanation…</p>}
                      {expErr && (
                        <p className="text-sm text-red-300">Explanation unavailable: {expErr}</p>
                      )}
                      {explanation && (
                        <div className="space-y-3">
                          <div className="text-sm text-night-100">
                            <b>{r.name}</b> — {levelVerdict(
                              (explanation.missing_critical ? 'DATA_UNAVAILABLE' : r.risk_level ?? 'DATA_UNAVAILABLE') as RiskLevel,
                              explanation.missing_critical,
                            )}
                          </div>
                          <div className="text-sm text-night-300">
                            <b>{r.name}</b> — {probHeadline(explanation.probability)} ({r.prediction_horizon ?? '0–24 h nowcast'}){' '}
                            from model {explanation.model_version}. Top model sensitivities:
                          </div>
                          <ul className="text-sm space-y-1">
                            {explanation.contributions.length === 0 && (
                              <li className="text-night-400">No single feature dominated this prediction.</li>
                            )}
                            {explanation.contributions.slice(0, 5).map((c) => {
                              const f = explanation.features.find((x) => x.feature === c.feature)
                              return (
                                <li key={c.feature}>
                                  {c.delta > 0 ? '▲' : '▼'} <b>{f?.label ?? c.feature}</b>{' '}
                                  {c.delta > 0 ? 'raised' : 'lowered'} the probability by{' '}
                                  <b>{fmtDelta(c.delta)}</b>
                                  {f?.value != null && (
                                    <span className="text-night-400">
                                      {' '}(now {f.value.toFixed(f.unit === 'ratio' ? 2 : 1)}{f.unit ? ` ${f.unit}` : ''}
                                      {f.hist_p90 != null ? `, historical 90th pct ${f.hist_p90.toFixed(2)}` : ''})
                                    </span>
                                  )}
                                </li>
                              )
                            })}
                          </ul>
                          {explanation.contributions.length > 0 && (
                            <div className="h-44 chart-rise d2">
                              <ResponsiveContainer>
                                <BarChart
                                  layout="vertical"
                                  data={explanation.contributions.slice(0, 5).map((c) => ({
                                    name: explanation.features.find((x) => x.feature === c.feature)?.label ?? c.feature,
                                    delta: Number(c.delta.toFixed(4)),
                                  }))}
                                  margin={{ left: 8, right: 24, top: 4, bottom: 4 }}
                                >
                                  {chartGradients(['#f59e0b', '#38bdf8'])}
                                  <XAxis type="number" hide />
                                  <YAxis
                                    type="category" dataKey="name" width={150}
                                    stroke="#5f7195" tick={AXIS_TICK}
                                  />
                                  {fancyTooltip()}
                                  <Bar
                                    dataKey="delta" name="model sensitivity"
                                    isAnimationActive animationDuration={900}
                                    animationEasing="ease-out"
                                    radius={[0, 4, 4, 0]}
                                  >
                                    {/* amber = raised the estimate, sky = lowered it (real ablation deltas) */}
                                    {explanation.contributions.slice(0, 5).map((c) => (
                                      <Cell key={c.feature} fill={grad(c.delta >= 0 ? '#f59e0b' : '#38bdf8')} />
                                    ))}
                                  </Bar>
                                </BarChart>
                              </ResponsiveContainer>
                            </div>
                          )}
                          <div className="text-xs text-night-400">
                            Freshness: {Object.entries(explanation.freshness).map(([k, v]) => `${k.replace('_', ' ')} ${v}`).join(' · ')}
                            {' · '}
                            <Link className="text-sky-400 hover:underline" to={`/location/${r.district_id}`}>
                              full page with feature table →
                            </Link>
                          </div>
                        </div>
                      )}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {rows.length === 0 && (
              <tr><td className="px-4 py-6 text-night-400" colSpan={7}>No risk data computed yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {rows.length > 80 && (
        <p className="text-xs text-night-400">Showing top 80 of {rows.length} districts by probability. Use the filter to find a specific one.</p>
      )}
    </div>
  )
}
