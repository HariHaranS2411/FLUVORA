import { useEffect, useMemo, useState } from 'react'
import { BarChart, ResponsiveContainer, XAxis, YAxis } from 'recharts'
import { api } from '../services/api'
import type { District, FloodHistory } from '../types'
import { Card } from '../components/Bits'
import { AXIS_COLOR, AXIS_TICK, chartGradients, fancyTooltip, glowBar, RiseOnHover, softGrid, useChartHover } from '../charts/Animated'

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export default function History() {
  const [states, setStates] = useState<{ state: string; districts: number }[]>([])
  const [state, setState] = useState('')
  const [districts, setDistricts] = useState<District[]>([])
  const [did, setDid] = useState<number | null>(null)
  const [flood, setFlood] = useState<FloodHistory | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.states().then(setStates).catch(() => {})
  }, [])

  useEffect(() => {
    if (state) api.districts(state).then(setDistricts).catch(() => {})
    else setDistricts([])
  }, [state])

  useEffect(() => {
    if (!did) { setFlood(null); return }
    setLoading(true)
    api.floodHistory(did)
      .then(setFlood)
      .catch(() => setFlood(null))
      .finally(() => setLoading(false))
  }, [did])

  const years = useMemo(
    () => (flood?.yearly ?? []).filter((y) => y.days > 30).map((y) => y.year),
    [flood],
  )
  const [year, setYear] = useState<number | null>(null)
  const activeYear = year ?? years[years.length - 1] ?? null

  const monthlyForYear = useMemo(() => {
    if (!activeYear || !flood) return []
    return MONTH_LABELS.map((label, i) => {
      const key = `${activeYear}-${String(i + 1).padStart(2, '0')}`
      const m = flood.monthly.find((x) => x.month === key)
      return { month: label, floodDays: m?.flood_days ?? 0, peak: m?.peak ?? 0 }
    })
  }, [flood, activeYear])

  const dailyYear = useMemo(() => {
    if (!flood || !activeYear) return []
    const inYear = flood.flood_days.filter((d) => d.date.startsWith(String(activeYear)))
    return inYear.map((d) => ({ date: d.date.slice(5), discharge: d.value }))
  }, [flood, activeYear])

  // pointer-proximity tracking so each graph "rises" when the mouse is near
  const [hover1, handlers1] = useChartHover()
  const [hover2, handlers2] = useChartHover()
  const [hover3, handlers3] = useChartHover()

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Historical Analysis</h1>
        <p className="text-sm text-night-400">
          Two separate record books are shown below: <b>officially reported flood events</b>{' '}
          (GDACS disaster reports) and <b>GloFAS-derived high-flow episodes</b> (the district's
          river crossing its modeled threshold). A high-flow episode is not automatically a
          confirmed flood — that is exactly why the two are listed separately. Pick a state,
          then a district.
        </p>
      </div>

      <div className="flex flex-wrap gap-2">
        <select className="border rounded-md px-3 py-2 text-sm bg-night-800" value={state}
                onChange={(e) => { setState(e.target.value); setDid(null); setFlood(null) }}>
          <option value="">Select state…</option>
          {states.map((s) => <option key={s.state} value={s.state}>{s.state}</option>)}
        </select>
        <select className="border rounded-md px-3 py-2 text-sm bg-night-800 disabled:opacity-50"
                value={did ?? ''} disabled={!state}
                onChange={(e) => setDid(Number(e.target.value) || null)}>
          <option value="">{state ? 'Select district…' : '— choose a state first —'}</option>
          {districts.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
        </select>
      </div>

      {!state && <p className="text-sm text-night-400">Choose a state to begin.</p>}
      {loading && <p className="text-sm text-night-400">Loading real flood history…</p>}

      {did && !loading && flood && (
        <>
          {flood.threshold_value == null ? (
            <Card title="Data availability">
              <p className="text-sm text-night-300">
                {flood.threshold_note} Available observations:
                {' '}{flood.available_from ?? 'none'} → {flood.available_to ?? 'none'}.
              </p>
            </Card>
          ) : (
            <>
              <div className="grid sm:grid-cols-3 gap-3 text-sm">
                <div className="bg-night-800 border rounded-xl p-3">
                  <div className="text-night-400 text-xs uppercase">Record period</div>
                  <div className="font-semibold">{flood.available_from} → {flood.available_to}</div>
                  <div className="text-xs text-night-500">source: GloFAS discharge</div>
                </div>
                <div className="bg-night-800 border rounded-xl p-3">
                  <div className="text-night-400 text-xs uppercase">High-flow threshold</div>
                  <div className="font-semibold">{flood.threshold_value} m³/s</div>
                  <div className="text-xs text-night-500">the river's own 99th percentile</div>
                  <div className="text-xs text-night-300 mt-1">
                    A “high-flow day” is a day the river carried more water than on 99% of the
                    days in this district's own record. This is a modeled threshold, not an
                    official flood declaration.
                  </div>
                </div>
                <div className="bg-night-800 border rounded-xl p-3">
                  <div className="text-night-400 text-xs uppercase">High-flow episodes on record</div>
                  <div className="font-semibold">{flood.episodes.length}</div>
                  <div className="text-xs text-night-500">{flood.flood_days.length} high-flow days total</div>
                </div>
                <div className="bg-night-800 border rounded-xl p-3">
                  <div className="text-night-400 text-xs uppercase">Officially reported events</div>
                  <div className="font-semibold">{flood.official_events.length}</div>
                  <div className="text-xs text-night-500">GDACS disaster reports matched to this district</div>
                </div>
              </div>

              <Card title="Which year do you want to see?">
                <div className="flex flex-wrap gap-2">
                  {years.map((y) => (
                    <button key={y}
                      className={`border rounded-md px-3 py-1.5 text-sm ${y === activeYear ? 'bg-sky-700 text-white border-sky-700' : 'hover:bg-night-900'}`}
                      onClick={() => setYear(y)}>
                      {y} {flood.yearly.find((x) => x.year === y)?.flood_days
                        ? `· ${flood.yearly.find((x) => x.year === y)?.flood_days} high-flow days`
                        : ''}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-night-400 mt-2">
                  The graphs below show {activeYear} for all 12 months. Years with fewer than 30
                  ingested days are hidden.
                </p>
              </Card>

              <Card title={`High-flow days per month — ${activeYear} (all 12 months)`}>
                <div className="h-60 chart-rise d1" {...handlers1}>
                  <RiseOnHover hovered={hover1}>
                    <ResponsiveContainer>
                      <BarChart data={monthlyForYear}>
                        {chartGradients(['#f97316'])}
                        {softGrid()}
                        <XAxis dataKey="month" stroke={AXIS_COLOR} tick={AXIS_TICK} />
                        <YAxis allowDecimals={false} width={30} stroke={AXIS_COLOR} tick={AXIS_TICK} />
                        {fancyTooltip(' days')}
                        {glowBar({ dataKey: 'floodDays', name: 'High-flow days', color: '#f97316', hovered: hover1 })}
                      </BarChart>
                    </ResponsiveContainer>
                  </RiseOnHover>
                </div>
              </Card>

              <Card title={`Peak river discharge per month — ${activeYear} (m³/s)`}>
                <div className="h-60 chart-rise d2" {...handlers2}>
                  <RiseOnHover hovered={hover2}>
                    <ResponsiveContainer>
                      <BarChart data={monthlyForYear}>
                        {chartGradients(['#a78bfa'])}
                        {softGrid()}
                        <XAxis dataKey="month" stroke={AXIS_COLOR} tick={AXIS_TICK} />
                        <YAxis width={44} stroke={AXIS_COLOR} tick={AXIS_TICK} />
                        {fancyTooltip(' m³/s')}
                        {glowBar({ dataKey: 'peak', name: 'Peak discharge (m³/s)', color: '#a78bfa', hovered: hover2 })}
                      </BarChart>
                    </ResponsiveContainer>
                  </RiseOnHover>
                </div>
              </Card>

              {dailyYear.length > 0 && (
                <Card title={`High-flow days in ${activeYear} (above the ${flood.threshold_value} m³/s threshold)`}>
                  <div className="h-52 chart-rise d3" {...handlers3}>
                    <RiseOnHover hovered={hover3}>
                      <ResponsiveContainer>
                        <BarChart data={dailyYear}>
                          {chartGradients(['#ef4444'])}
                          {softGrid()}
                          <XAxis dataKey="date" ticks={[]} stroke={AXIS_COLOR} tick={AXIS_TICK} />
                          <YAxis width={44} stroke={AXIS_COLOR} tick={AXIS_TICK} />
                          {fancyTooltip(' m³/s')}
                          {glowBar({ dataKey: 'discharge', name: 'Discharge on flood day (m³/s)', color: '#ef4444', hovered: hover3 })}
                        </BarChart>
                      </ResponsiveContainer>
                    </RiseOnHover>
                  </div>
                </Card>
              )}

              <Card title="① GloFAS-derived high-flow episodes (modeled threshold exceedances)">
                {flood.episodes.length ? (
                  <p className="text-sm text-night-200 mb-3">
                    The river crossed its high-flow threshold <b>{flood.episodes.length} time{flood.episodes.length === 1 ? '' : 's'}</b>{' '}
                    ({flood.flood_days.length} days in total) between {flood.available_from} and{' '}
                    {flood.available_to}. These are modeled hydrological episodes — not
                    automatically officially reported floods.
                  </p>
                ) : (
                  <p className="text-sm text-night-400">
                    No high-flow days on record for this district in the ingested period.
                  </p>
                )}
                {flood.episodes.length ? (
                  <ul className="text-sm space-y-2 max-h-96 overflow-y-auto">
                    {flood.episodes.slice().reverse().map((e) => (
                      <li key={e.start} className="border-l-2 border-orange-500 pl-3">
                        <span className="font-medium">{e.start}</span>
                        {e.end !== e.start ? ` → ${e.end}` : ''} — peak{' '}
                        <b>{e.peak_m3s} m³/s</b>, {e.days} day{e.days > 1 ? 's' : ''}
                        <div className="text-night-300 text-xs">
                          the river ran at {(e.peak_m3s / flood.threshold_value!).toFixed(1)}×
                          this district's high-flow threshold
                          {e.conditions.max_daily_rain_mm != null
                            ? ` · rain during episode: ${e.conditions.max_daily_rain_mm} mm/day peak`
                            : ''}
                          {e.conditions.mean_soil_moisture != null
                            ? ` · soil: ${e.conditions.mean_soil_moisture} m³/m³`
                            : ''}
                        </div>
                        {e.officially_reported && e.event_name ? (
                          <div className="text-sky-300">
                            ⚠️ officially reported: {e.event_name}
                            {e.alert_level ? ` · ${e.alert_level} alert` : ''}
                            {e.glide ? ` · GLIDE ${e.glide}` : ''}
                            <span className="text-night-500"> ({e.name_source})</span>
                          </div>
                        ) : (
                          <div className="text-night-500 text-xs">
                            official event name unavailable — no matching GDACS report for this
                            episode (shown from observed discharge only)
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <p className="text-xs text-night-400 mt-2">{flood.threshold_note}</p>
              </Card>

              <Card title="② Officially reported flood events (GDACS archive)">
                {flood.official_events.length ? (
                  <ul className="text-sm space-y-2">
                    {flood.official_events.slice().reverse().map((e) => (
                      <li key={e.event_id} className="border-l-2 border-sky-600 pl-3">
                        <span className="font-medium">{e.from_date}</span>
                        {e.to_date && e.to_date !== e.from_date ? ` → ${e.to_date}` : ''} —{' '}
                        <b>{e.name}</b>
                        <span className="text-night-500">
                          {' '}({e.source}{e.alert_level ? `, ${e.alert_level} alert` : ''}
                          {e.glide ? `, GLIDE ${e.glide}` : ''})
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-sm text-night-400">
                    No official flood reports are recorded for this district in the GDACS
                    archive for the ingested period. Absence of reports is not evidence of
                    absence of flooding.
                  </p>
                )}
              </Card>

              <Card title="Year-by-year summary">
                <table className="min-w-full text-sm">
                  <thead className="bg-night-700 text-left text-xs uppercase text-night-400">
                    <tr>
                      <th className="px-3 py-1.5">Year</th>
                      <th className="px-3 py-1.5">Days ingested</th>
                      <th className="px-3 py-1.5">High-flow days</th>
                      <th className="px-3 py-1.5">Peak discharge (m³/s)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {flood.yearly.map((y) => (
                      <tr key={y.year} className="border-t border-night-600">
                        <td className="px-3 py-1.5 font-medium">{y.year}</td>
                        <td className="px-3 py-1.5">{y.days}</td>
                        <td className="px-3 py-1.5">{y.flood_days}</td>
                        <td className="px-3 py-1.5">{Math.round(y.peak)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Card>
            </>
          )}
        </>
      )}
    </div>
  )
}
