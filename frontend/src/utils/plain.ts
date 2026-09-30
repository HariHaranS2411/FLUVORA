/**
 * Plain-language layer: turns real technical values into everyday sentences.
 *
 * Rules:
 *  - Every phrase is computed from real values only — nothing is invented.
 *  - The model's target is HYDROLOGICAL (GloFAS high-flow threshold exceedance),
 *    so wording says "high-flow conditions" / "threshold exceedance", never
 *    "a flood will happen". Probabilities are model estimates, not certainties.
 *  - Missing data stays honest ("no data", "unavailable"); never guessed.
 *  - Data quality, model estimate and uncertainty are shown separately.
 *  - The exact numbers stay visible in the UI; these helpers add the meaning.
 */

// ---------- probability / risk level ----------

export type RiskLevel = 'LOW' | 'MODERATE' | 'HIGH' | 'CRITICAL' | 'DATA_UNAVAILABLE'

export interface ModelThresholds {
  moderate: number
  high: number
  critical: number
}

/** Level from a probability using the model-card thresholds (in % form). */
export function levelFor(
  p: number | null | undefined,
  thr: ModelThresholds | null,
): RiskLevel {
  if (p == null || !thr) return 'DATA_UNAVAILABLE'
  if (p >= thr.critical) return 'CRITICAL'
  if (p >= thr.high) return 'HIGH'
  if (p >= thr.moderate) return 'MODERATE'
  return 'LOW'
}

/** One-sentence everyday verdict for the current level (honest meaning). */
export function levelVerdict(level: RiskLevel, missingCritical: boolean): string {
  if (missingCritical || level === 'DATA_UNAVAILABLE') {
    return 'Risk assessment unavailable due to insufficient data — some core measurements (rain, soil or river) are missing for this district.'
  }
  switch (level) {
    case 'LOW':
      return 'No high-flow signals today. The river, rain and soil data all look normal for this district.'
    case 'MODERATE':
      return 'Some high-flow signals are present. Conditions are leaning toward the district’s high-flow threshold — a day to stay aware.'
    case 'HIGH':
      return 'Strong high-flow signals. The model estimates conditions near the level at which this district’s river crosses its high-flow threshold.'
    case 'CRITICAL':
      return 'Severe high-flow signals. The model estimates the river is very likely above this district’s high-flow threshold today.'
  }
}

/** What a normal person can usefully do at this level. */
export function levelAdvice(level: RiskLevel, missingCritical: boolean): string {
  if (missingCritical || level === 'DATA_UNAVAILABLE') {
    return 'This page will update automatically as soon as the data returns. Follow official IMD/NDMA channels in the meantime.'
  }
  switch (level) {
    case 'LOW':
      return 'No special action needed — normal monsoon-season awareness is enough.'
    case 'MODERATE':
      return 'Keep an eye on local news, and avoid walking or driving through waterlogged spots during heavy rain.'
    case 'HIGH':
      return 'Stay alert: avoid flooded roads, drains and river banks, and follow IMD/NDMA and district administration updates.'
    case 'CRITICAL':
      return 'If you are in a low-lying or flood-prone area, move to safety and follow official evacuation guidance.'
  }
}

/**
 * Human phrase for the model estimate — always framed as a model estimate of
 * threshold exceedance, never as "the chance a flood happens".
 */
export function chancePhrase(p: number | null | undefined): string {
  if (p == null) return 'no estimate available'
  if (p <= 0) return 'essentially zero'
  const oneIn = Math.round(1 / p)
  return `about a 1-in-${oneIn} chance`
}

/** Honest headline for any probability display. */
export function probHeadline(p: number | null | undefined): string {
  if (p == null) return 'Model estimate: unavailable'
  return `Model-estimated probability of high-flow conditions: ${(p * 100).toFixed(p < 0.01 ? 2 : 1)}%`
}

// ---------- trend ----------

export function trendArrow(trend: string | null | undefined): string {
  switch (trend) {
    case 'increasing':
    case 'rising':
      return '↑ Increasing'
    case 'decreasing':
    case 'falling':
      return '↓ Decreasing'
    case 'stable':
      return '→ Stable'
    default:
      return '— unknown'
  }
}

// ---------- contributions (model sensitivities) ----------

/**
 * Probability deltas are often tiny (0.0045 = 0.45%); rounding to whole
 * percent shows "0%" and looks broken. Format with the precision that fits.
 */
export function fmtDelta(delta: number | null | undefined): string {
  if (delta == null) return 'N/A'
  const a = Math.abs(delta) * 100
  if (a === 0) return '0%'
  if (a < 0.05) return '<0.1%'
  if (a < 10) return a.toFixed(1) + '%'
  return Math.round(a) + '%'
}

// ---------- everyday condition classes ----------

/** IMD-style daily rainfall classes (mm/day). */
export function rainClass(mm: number | null | undefined): string {
  if (mm == null) return 'no rainfall data'
  if (mm < 0.1) return 'no rain'
  if (mm < 2.5) return 'very light rain'
  if (mm < 15.6) return 'light rain'
  if (mm < 64.5) return 'moderate rain'
  if (mm < 115.6) return 'rather heavy rain'
  if (mm < 204.5) return 'heavy rain'
  return 'very heavy rain'
}

/** Where a value sits in the district's own history (from model-card stats). */
export function pctlWord(
  v: number | null | undefined,
  p50?: number | null,
  p90?: number | null,
  p99?: number | null,
): string {
  if (v == null) return 'no data'
  if (p99 != null && v >= p99) return 'extremely high — more than on 99% of historical days'
  if (p90 != null && v >= p90) return 'unusually high — more than on 90% of historical days'
  if (p50 != null && v >= p50) return 'about the historical level for this district'
  return 'below the historical median for this district'
}

export function riverWord(ratio: number | null | undefined): string {
  if (ratio == null) return 'no seasonal comparison available'
  if (ratio < 0.25) return 'very low for this time of year'
  if (ratio < 0.6) return 'below normal for this time of year'
  if (ratio < 1.5) return 'about normal for this time of year'
  if (ratio < 3) return 'higher than normal for this time of year'
  if (ratio < 6) return 'much higher than normal for this time of year'
  return 'exceptionally high for this time of year'
}

export function daysAgoPhrase(days: number): string {
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 60) return `${days} days ago`
  if (days < 730) return `${Math.round(days / 30)} months ago`
  return `${(days / 365).toFixed(1)} years ago`
}

// ---------- full story ----------

export interface PlainFeature {
  feature: string
  label: string
  unit: string
  value: number | null
  hist_median?: number
  hist_p90?: number
  hist_p99?: number
}

export interface PlainExplanation {
  probability: number | null
  missing_critical: boolean
  data_quality?: string
  prediction_horizon?: string
  trend?: string
  features: PlainFeature[]
  signals?: {
    rain?: { rain_1d_mm?: number | null; rain_7d_mm?: number | null; description?: string }
    soil?: { soil_moisture_m3m3?: number | null; description?: string }
    river?: {
      current_m3s?: number | null
      seasonal_mean_m3s?: number | null
      ratio_to_seasonal_mean?: number | null
      trend?: string
    } | null
    recent_high_flow?: { exceedance_30d?: number; days_since_last?: number }
  }
  last_flood?: {
    date: string
    days_ago: number
  } | null
}

/**
 * The everyday story for the current estimate. Every sentence is derived from
 * the real explanation payload + real model-card thresholds.
 */
export function plainStory(ex: PlainExplanation, thr: ModelThresholds | null): {
  level: RiskLevel
  verdict: string
  conditions: string[]
  advice: string
  chance: string
} {
  const level = levelFor(ex.probability, thr)
  const conditions: string[] = []
  const f = (name: string) => ex.features.find((x) => x.feature === name)

  // rain
  const rain = ex.signals?.rain?.rain_1d_mm ?? featVal(ex, 'rain_1d')
  const rain7 = ex.signals?.rain?.rain_7d_mm ?? featVal(ex, 'rain_7d')
  conditions.push(
    rain == null
      ? 'Rainfall today: no data.'
      : rain < 0.1
        ? 'Rainfall today: none recorded.'
        : `Rainfall today: ${rain} mm — ${rainClass(rain)}.`,
  )
  if (rain7 != null) {
    const f7 = f('rain_7d')
    conditions.push(
      `7-day rainfall: ${rain7} mm — ${pctlWord(rain7, f7?.hist_median, f7?.hist_p90, f7?.hist_p99)}.`,
    )
  }

  // soil — honest relative wording, no arbitrary "% soaked"
  const soilDesc = ex.signals?.soil?.description
  const sm = ex.signals?.soil?.soil_moisture_m3m3 ?? featVal(ex, 'soil_moisture')
  if (soilDesc) conditions.push(soilDesc)
  else if (sm != null) {
    const sat = f('soil_saturation')
    conditions.push(
      `Soil moisture: ${sm} m³/m³ — ${pctlWord(sat?.value ?? null, sat?.hist_median, sat?.hist_p90, sat?.hist_p99)}.`,
    )
  } else conditions.push('Soil moisture: no data.')

  // river — × baseline wording
  const river = ex.signals?.river
  if (river?.ratio_to_seasonal_mean != null && river.current_m3s != null) {
    conditions.push(
      `River discharge is ${river.ratio_to_seasonal_mean}× the seasonal baseline (${river.current_m3s} m³/s vs ${river.seasonal_mean_m3s} m³/s) — ${riverWord(river.ratio_to_seasonal_mean)}${river.trend && river.trend !== 'stable' ? `, and ${river.trend}` : ''}.`,
    )
  } else if (river?.current_m3s != null) {
    conditions.push(`River discharge: ${river.current_m3s} m³/s (no seasonal baseline available for comparison).`)
  } else {
    conditions.push('River discharge: no data.')
  }

  // recent high-flow context
  const hf = ex.signals?.recent_high_flow
  if (hf?.exceedance_30d != null && hf.exceedance_30d > 0) {
    conditions.push(`The river crossed its high-flow threshold on ${hf.exceedance_30d} day${hf.exceedance_30d === 1 ? '' : 's'} in the past 30 days.`)
  }
  const lf = ex.last_flood
  if (lf) {
    conditions.push(`The last high-flow episode here was ${daysAgoPhrase(lf.days_ago)} (${new Date(lf.date).toLocaleDateString()}).`)
  }

  return {
    level,
    verdict: levelVerdict(level, ex.missing_critical),
    conditions,
    advice: levelAdvice(level, ex.missing_critical),
    chance: chancePhrase(ex.probability),
  }
}

function featVal(ex: PlainExplanation, feature: string): number | null {
  const f = ex.features.find((x) => x.feature === feature)
  return f && f.value != null ? f.value : null
}
