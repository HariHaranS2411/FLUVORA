import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  LineChart, ResponsiveContainer, XAxis, YAxis,
} from 'recharts'
import { chartGradients, fancyTooltip, glowArea, glowLine, RiseOnHover, softGrid, useChartHover } from '../charts/Animated'
import { api } from '../services/api'
import type {
  Assessment, District, Explanation, HistoricalSeries, LatestData, ModelInfo,
} from '../types'
import { Card, Freshness, NA, RiskBadge } from '../components/Bits'
import PlainStory from '../components/PlainStory'
import { fmtDelta, probHeadline, rainClass, riverWord, trendArrow, type ModelThresholds } from '../utils/plain'

export default function Location() {
  const { id } = useParams()
  const did = Number(id)
  const [district, setDistrict] = useState<District | null>(null)
  const [assessment, setAssessment] = useState<Assessment | null>(null)
  const [latest, setLatest] = useState<LatestData | null>(null)
  const [hist, setHist] = useState<HistoricalSeries | null>(null)
  const [explain, setExplain] = useState<Explanation | null>(null)
  const [showExplain, setShowExplain] = useState(false)
  const [note, setNote] = useState('')
  const [latestErr, setLatestErr] = useState<string | null>(null)
  const [thresholds, setThresholds] = useState<ModelThresholds | null>(null)
  // pointer-proximity tracking so the line charts glow and thicken on hover
  const [rainHover, rainHoverHandlers] = useChartHover()
  const [disHover, disHoverHandlers] = useChartHover()

  useEffect(() => {
    api.modelInfo()
      .then((m: ModelInfo) => setThresholds(m.thresholds))
      .catch(() => setThresholds(null))
  }, [])

  useEffect(() => {
    if (!did) return
    api.risk(did).then((r) => {
      setDistrict(r.district)
      setAssessment(r.assessment)
      setNote(r.note ?? '')
    }).catch(() => {})
    api.latestData(did).then(setLatest).catch((e) => setLatestErr(String(e.message || e)))
    api.historical(did, 365).then(setHist).catch(() => {})
    api.explain(did).then(setExplain).catch(() => {})
  }, [did])

  if (!district) return <p className="text-night-400">Loading…</p>

  const lastVal = (v: string) => {
    const arr = latest?.variables[v]
    if (!arr || arr.length === 0) return null
    return arr[0]
  }

  const rainSeries = (hist?.series['rain_mm'] ?? []).map((x) => ({ date: x.date, rain: x.value }))
  const disSeries = (hist?.series['river_discharge'] ?? []).map((x) => ({ date: x.date, discharge: x.value }))

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{district.name}</h1>
          <p className="text-sm text-night-400">{district.state} · district</p>
        </div>
        <div className="text-right">
          <RiskBadge level={assessment?.risk_level ?? null} />
          {assessment?.risk_level && assessment.state_label && (
            <p className="text-sm text-night-300 mt-1">{assessment.state_label}</p>
          )}
          {assessment?.probability != null && (
            <p className="text-sm text-night-300 mt-1">{probHeadline(assessment.probability)}</p>
          )}
          {assessment?.prediction_horizon && (
            <p className="text-xs text-night-400 mt-0.5">Horizon: {assessment.prediction_horizon}</p>
          )}
        </div>
      </div>
      {assessment?.probability_meaning && (
        <p className="text-xs text-night-400 -mt-2">{assessment.probability_meaning}</p>
      )}

      {note && (
        <div className="bg-panel-amber border border-amber-700/60 text-amber-200 rounded-lg p-3 text-sm">
          ⚠️ {note}
        </div>
      )}

      {explain && !explain.missing_critical && (
        <PlainStory ex={explain} thresholds={thresholds} />
      )}

      <div className="grid lg:grid-cols-2 gap-4">
        <Card title="Current conditions (real observations)">
          {latestErr && (
            <p className="mb-2 text-sm text-amber-200 bg-panel-amber border border-amber-700/60 rounded p-2">
              ⚠️ Could not load observations ({latestErr}). This is a temporary failure — the
              values below are N/A because the request failed, not because the district has no data.
            </p>
          )}
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between gap-2">
              <dt>Latest rainfall (mm)</dt>
              <dd>
                {lastVal('rain_mm')
                  ? `${lastVal('rain_mm')!.value} ${lastVal('rain_mm')!.unit} — ${rainClass(lastVal('rain_mm')!.value)}`
                  : <NA />}
              </dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt>
                Soil moisture 0–7 cm (m³/m³){' '}
                {explain?.signals?.soil?.description && (
                  <span className="text-night-400 text-xs">
                    · {explain.signals.soil.description.replace('Soil moisture is currently ', '')}
                  </span>
                )}
              </dt>
              <dd>{lastVal('soil_moisture_0_7cm') ? `${lastVal('soil_moisture_0_7cm')!.value.toFixed(3)}` : <NA />}</dd>
            </div>
            <div className="flex justify-between gap-2">
              <dt>
                River discharge (m³/s){' '}
                {explain?.signals?.river?.ratio_to_seasonal_mean != null && (
                  <span className="text-night-400 text-xs">
                    · {explain.signals.river.ratio_to_seasonal_mean}× seasonal baseline — {riverWord(explain.signals.river.ratio_to_seasonal_mean)}
                  </span>
                )}
              </dt>
              <dd>{lastVal('river_discharge') ? `${lastVal('river_discharge')!.value.toFixed(0)} ${lastVal('river_discharge')!.unit}` : <NA />}</dd>
            </div>
            {(['Rainfall', 'Soil moisture', 'River discharge'] as const).map((label, i) => {
              const key = ['rain_mm', 'soil_moisture_0_7cm', 'river_discharge'][i]
              const o = lastVal(key)
              return (
                <div key={label} className="flex justify-between gap-2">
                  <dt className="text-night-400">{label} freshness</dt>
                  <dd><Freshness status={o ? freshOf(o.observed_at) : 'unavailable'} /></dd>
                </div>
              )
            })}
            <div className="flex justify-between gap-2">
              <dt className="text-night-400">Sources</dt>
              <dd className="text-right text-xs text-night-300">
                {uniqueSources(latest)}
              </dd>
            </div>
          </dl>
        </Card>

        <Card title="Historical pattern (real GDACS events for this district)">
          {hist && hist.flood_events.length > 0 ? (
            <ul className="text-sm space-y-2">
              {hist.flood_events.slice(-6).reverse().map((e) => (
                <li key={e.event_id} className="border-l-2 border-sky-500 pl-3">
                  <span className="font-medium">{e.from_date}</span>
                  {e.to_date && e.to_date !== e.from_date ? ` → ${e.to_date}` : ''} — {e.name}
                  <span className="text-night-500"> ({e.source}{e.alert_level ? `, ${e.alert_level}` : ''})</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-night-400">
              No flood reports recorded for this district in the GDACS archive (2015–2026).
              Absence of reports is not evidence of absence of risk.
            </p>
          )}
          {assessment?.history_similarity && (
            <p className="mt-3 text-sm border-t pt-3">
              <span className="text-night-400">Historical similarity: </span>
              {assessment.history_similarity}
            </p>
          )}
        </Card>

        <Card title="Rainfall history (ERA5, past year)">
          {rainSeries.length > 0 ? (
            <div className="h-56 chart-rise d1" {...rainHoverHandlers}>
              <RiseOnHover hovered={rainHover}>
                <ResponsiveContainer>
                  <LineChart data={rainSeries}>
                    {chartGradients(['#38bdf8'], true)}
                    {softGrid()}
                    <XAxis dataKey="date" ticks={[]} stroke="#5f7195" tick={{ fill: '#8395b8', fontSize: 11 }} />
                    <YAxis width={40} stroke="#5f7195" tick={{ fill: '#8395b8', fontSize: 11 }} />
                    {fancyTooltip(' mm')}
                    {glowArea({ dataKey: 'rain', name: 'Rainfall', color: '#38bdf8' })}
                    {glowLine({ dataKey: 'rain', name: 'Rainfall', color: '#38bdf8', hovered: rainHover })}
                  </LineChart>
                </ResponsiveContainer>
              </RiseOnHover>
            </div>
          ) : (
            <NA>Data unavailable</NA>
          )}
        </Card>

        <Card title="River discharge history (GloFAS, past year)">
          {disSeries.length > 0 ? (
            <div className="h-56 chart-rise d2" {...disHoverHandlers}>
              <RiseOnHover hovered={disHover}>
                <ResponsiveContainer>
                  <LineChart data={disSeries}>
                    {chartGradients(['#a78bfa'], true)}
                    {softGrid()}
                    <XAxis dataKey="date" ticks={[]} stroke="#5f7195" tick={{ fill: '#8395b8', fontSize: 11 }} />
                    <YAxis width={40} stroke="#5f7195" tick={{ fill: '#8395b8', fontSize: 11 }} />
                    {fancyTooltip(' m³/s')}
                    {glowArea({ dataKey: 'discharge', name: 'Discharge', color: '#a78bfa' })}
                    {glowLine({ dataKey: 'discharge', name: 'Discharge', color: '#a78bfa', hovered: disHover })}
                  </LineChart>
                </ResponsiveContainer>
              </RiseOnHover>
            </div>
          ) : (
            <NA>Data unavailable</NA>
          )}
        </Card>
      </div>

      {assessment && (
        <Card title={`Why is risk at this level? (${assessment.risk_level ?? 'NO DATA'} ${trendArrow(assessment.trend)})`}>
          <div className="grid sm:grid-cols-2 gap-x-8 gap-y-2 text-sm">
            <div className="flex justify-between gap-2"><span className="text-night-400">Current rainfall</span>
              <b>{explain?.signals?.rain?.rain_1d_mm != null ? `${explain.signals.rain.rain_1d_mm} mm` : <NA />}</b></div>
            <div className="flex justify-between gap-2"><span className="text-night-400">7-day rainfall</span>
              <b>{explain?.signals?.rain?.rain_7d_mm != null ? `${explain.signals.rain.rain_7d_mm} mm` : <NA />}</b></div>
            <div className="flex justify-between gap-2"><span className="text-night-400">Soil moisture</span>
              <b>{explain?.signals?.soil?.soil_moisture_m3m3 != null ? `${explain.signals.soil.soil_moisture_m3m3} m³/m³` : <NA />}</b></div>
            <div className="flex justify-between gap-2"><span className="text-night-400">River condition</span>
              <b>{explain?.signals?.river?.ratio_to_seasonal_mean != null
                ? `${explain.signals.river.ratio_to_seasonal_mean}× seasonal baseline`
                : <NA />}</b></div>
            <div className="flex justify-between gap-2"><span className="text-night-400">River trend</span>
              <b>{explain?.signals?.river?.trend ?? <NA />}</b></div>
            <div className="flex justify-between gap-2"><span className="text-night-400">High-flow days, past 30 d</span>
              <b>{explain?.signals?.recent_high_flow?.exceedance_30d ?? <NA />}</b></div>
            <div className="flex justify-between gap-2"><span className="text-night-400">Days since last high-flow episode</span>
              <b>{explain?.signals?.recent_high_flow?.days_since_last ?? <NA />}</b></div>
            <div className="flex justify-between gap-2"><span className="text-night-400">Forecast signal</span>
              <b>Not available in this system</b></div>
            <div className="flex justify-between gap-2"><span className="text-night-400">Model estimate ({assessment.prediction_horizon ?? '0–24 h'})</span>
              <b>{assessment.probability != null ? `${(assessment.probability * 100).toFixed(assessment.probability < 0.01 ? 2 : 1)}%` : <NA />}</b></div>
            <div className="flex justify-between gap-2"><span className="text-night-400">Data quality</span>
              <b>{assessment.data_quality ?? <NA />}</b></div>
          </div>
          {explain?.signals?.rain?.description && (
            <p className="mt-3 text-sm">Rain: {explain.signals.rain.description}.</p>
          )}
          {explain?.signals?.soil?.description && (
            <p className="text-sm">{explain.signals.soil.description}</p>
          )}
          {explain?.signals?.river && (
            <p className="text-sm">
              River discharge is currently{' '}
              {explain.signals.river.ratio_to_seasonal_mean != null
                ? `${explain.signals.river.ratio_to_seasonal_mean}× the seasonal baseline and ${explain.signals.river.trend}.`
                : `${explain.signals.river.current_m3s} m³/s (no seasonal baseline available).`}
            </p>
          )}
          <p className="mt-2 text-sm text-night-300">Model sensitivities (drivers):</p>
          <ul className="text-sm space-y-1">
            {assessment.drivers.map((d, i) => (
              <li key={i}>✓ {d}</li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-night-400">
            These are model sensitivities (associations the model learned), not claims of causation.
          </p>
          <div className="mt-3 grid sm:grid-cols-3 gap-3 text-sm">
            <div><span className="text-night-400">Trend: </span>{trendArrow(assessment.trend)}</div>
            <div><span className="text-night-400">Model: </span>{assessment.model_version}</div>
            <div><span className="text-night-400">Last updated: </span>
              {new Date(assessment.last_updated ?? assessment.computed_at).toLocaleString()}</div>
          </div>
          <p className="mt-2 text-xs text-night-400">
            Data cut: {assessment.data_cut ? new Date(assessment.data_cut).toLocaleString() : 'N/A'} ·
            Data quality and model estimate are reported separately and never merged.
          </p>
        </Card>
      )}

      {assessment && (
        <Card title={`Last flood here: ${assessment ? lastFloodSummary(explain) : ''}`}>
          {explain?.last_flood ? (
            <div className="text-sm space-y-3">
              <p>
                <b>{new Date(explain.last_flood.date).toLocaleDateString()}</b>{' '}
                ({explain.last_flood.days_ago} days ago) — {explain.last_flood.kind}
                {explain.last_flood.event_name ? `: ${explain.last_flood.event_name}` : ''}
                {explain.last_flood.glide ? ` (GLIDE ${explain.last_flood.glide})` : ''}
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
                        <td className="px-2 py-1">{fmtVal(explain.last_flood!.conditions_during_flood[k as keyof typeof explain.last_flood.conditions_during_flood])}</td>
                        <td className="px-2 py-1 font-semibold">{fmtVal(explain.last_flood!.conditions_now[k as keyof typeof explain.last_flood.conditions_now])}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-night-400">{explain.last_flood.note}</p>
            </div>
          ) : (
            <p className="text-sm text-night-400">
              No reported flood (GDACS) and no flood-level river day (GloFAS) is on record for
              this district, so there is no local flood event to compare today's conditions
              against. The model still uses regional rainfall/soil/discharge patterns plus this
              district's terrain and season.
            </p>
          )}
        </Card>
      )}

      <Card title="How this prediction was computed">
        {explain ? (
          <div className="space-y-3">
            <button
              className="text-sm text-sky-400 hover:underline"
              onClick={() => setShowExplain((v) => !v)}
            >
              {showExplain ? 'Hide' : 'Show'} the full step-by-step calculation
            </button>
            <ol className="text-sm space-y-1 text-night-300 list-decimal list-inside">
              <li>Latest real observations are fetched for this district (rainfall, soil moisture, river discharge) — see timestamps above.</li>
              <li>The same features used in training are computed from them (rainfall windows, soil saturation, discharge ratios, terrain, season, past flood reports).</li>
              <li>The trained model ({explain.model_version}) converts these features into a probability using patterns learned from 2020–2023 history.</li>
              <li>Documented thresholds decide the level: the probability is compared against MODERATE/HIGH/CRITICAL cutoffs derived on validation data (shown on the button panel below).</li>
            </ol>
            {showExplain && (
              <div className="space-y-4 border-t pt-3">
                <div>
                  <h3 className="text-sm font-semibold mb-1">Step 2 — the actual feature values used</h3>
                  <div className="overflow-x-auto">
                    <table className="min-w-full text-xs">
                      <thead className="bg-night-700 text-left uppercase text-night-400">
                        <tr>
                          <th className="px-2 py-1">Feature</th>
                          <th className="px-2 py-1">Value now</th>
                          <th className="px-2 py-1">Historical median</th>
                          <th className="px-2 py-1">90th pct</th>
                          <th className="px-2 py-1">99th pct</th>
                        </tr>
                      </thead>
                      <tbody>
                        {explain.features.map((f) => (
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
                </div>
                <div>
                  <h3 className="text-sm font-semibold mb-1">Step 3 — what moved the model estimate (model sensitivities)</h3>
                  <ul className="text-sm space-y-1">
                    {explain.contributions.length === 0 && <li className="text-night-400">No single feature dominated this prediction.</li>}
                    {explain.contributions.slice(0, 8).map((c) => {
                      const f = explain.features.find((x) => x.feature === c.feature)
                      const sign = c.delta > 0 ? 'raised' : 'lowered'
                      return (
                        <li key={c.feature}>
                          {c.delta > 0 ? '▲' : '▼'} <b>{f?.label ?? c.feature}</b> {sign} the estimate by{' '}
                          <b>{fmtDelta(c.delta)}</b>
                        </li>
                      )
                    })}
                  </ul>
                  <p className="text-xs text-night-400 mt-2">{explain.method_notes.contributions}</p>
                </div>
                {explain.historical_analogues && (
                  <div>
                    <h3 className="text-sm font-semibold mb-1">Step 4 — similar past high-flow conditions (historical analogues)</h3>
                    <p className="text-sm text-night-300 mb-2">
                      Today's conditions most resemble these real past high-flow episodes in this
                      district (similarity computed by transparent normalized distance over{' '}
                      {explain.historical_analogues.features_used.join(', ')}):
                    </p>
                    <div className="overflow-x-auto">
                      <table className="min-w-full text-xs">
                        <thead className="bg-night-700 text-left uppercase text-night-400">
                          <tr>
                            <th className="px-2 py-1">Date</th>
                            <th className="px-2 py-1">7-day rain (mm)</th>
                            <th className="px-2 py-1">Soil saturation</th>
                            <th className="px-2 py-1">Discharge × seasonal mean</th>
                            <th className="px-2 py-1">Similarity</th>
                          </tr>
                        </thead>
                        <tbody>
                          {explain.historical_analogues.episodes.map((a) => (
                            <tr key={a.date} className="border-t border-night-600">
                              <td className="px-2 py-1 font-medium">{a.date}</td>
                              <td className="px-2 py-1">{a.rain_7d_mm ?? <NA />}</td>
                              <td className="px-2 py-1">{a.soil_saturation ?? <NA />}</td>
                              <td className="px-2 py-1">{a.discharge_ratio_mean ?? <NA />}</td>
                              <td className="px-2 py-1 font-semibold">{a.similarity_pct}%</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="text-xs text-night-400 mt-2">
                      {explain.historical_analogues.n_similar_episodes} of{' '}
                      {explain.historical_analogues.n_past_episodes} past high-flow days were
                      ≥70% similar ({explain.historical_analogues.share_of_similar_episodes_pct}%).
                      Similarity is descriptive: it does NOT mean a flood will occur. {explain.historical_analogues.method}
                    </p>
                  </div>
                )}
                <div>
                  <h3 className="text-sm font-semibold mb-1">Data limitations (read this)</h3>
                  <ul className="text-xs text-night-300 list-disc list-inside space-y-1">
                    <li>Risk is assessed at DISTRICT level using district-average data — not neighborhood-level.</li>
                    <li>The model predicts modeled high-flow threshold exceedance (GloFAS), not officially confirmed floods.</li>
                    <li>The model estimate is not certainty; it is not calibrated as a real-world flood probability.</li>
                    <li>External data (ERA5, GloFAS, GDACS) can be delayed or unavailable; missing data is shown, never filled.</li>
                    <li>Historical similarity does not guarantee future flooding.</li>
                    <li>Rainfall/discharge persistence makes prediction partly easier than genuine anticipation.</li>
                    <li>Flash floods triggered by localized cloudbursts can be missed by district-scale data.</li>
                    <li>Short-term rain forecast is not ingested; there is no forecast signal yet.</li>
                  </ul>
                </div>
                <div>
                  <h3 className="text-sm font-semibold mb-1">Risk-level thresholds (derived on validation data)</h3>
                  <p className="text-xs text-night-400">{explain.method_notes.history}</p>
                </div>
              </div>
            )}
          </div>
        ) : (
          <NA>Explanation unavailable (model not loaded)</NA>
        )}
      </Card>
    </div>
  )
}

function lastFloodSummary(e: Explanation | null): string {
  if (!e?.last_flood) return 'no local flood on record'
  return `${e.last_flood.days_ago} days ago`
}

function fmtVal(v: number | null): string {
  return v == null ? 'N/A' : String(v)
}

function freshOf(iso: string): string {
  const age = Date.now() - new Date(iso).getTime()
  if (age < 36 * 3600 * 1000) return 'fresh'
  if (age < 8 * 24 * 3600 * 1000) return 'stale'
  return 'unavailable'
}

function uniqueSources(latest: LatestData | null): string {
  const s = new Set<string>()
  Object.values(latest?.variables ?? {}).forEach((arr) => arr.forEach((o) => s.add(o.source)))
  return s.size ? [...s].join(', ') : 'none yet'
}
