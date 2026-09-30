export interface RiskMapItem {
  district_id: number
  name: string
  state: string
  lat: number
  lon: number
  probability: number | null
  risk_probability: number | null
  risk_level: string | null
  state_label: string | null
  prediction_horizon: string | null
  probability_meaning: string | null
  data_quality: string | null
  trend: string
  confidence: number | null
  data_cut: string | null
  last_updated: string | null
  missing_critical: boolean
}

export interface RiskMap {
  computed_at: string | null
  districts: RiskMapItem[]
  counts: {
    critical: number
    high: number
    moderate: number
    low: number
    data_unavailable: number
  } | null
}

export interface District {
  id: number
  name: string
  state: string
  lat: number | null
  lon: number | null
}

export interface Assessment {
  probability: number | null
  risk_probability: number | null
  risk_level: string | null
  state_label: string | null
  probability_meaning: string | null
  prediction_horizon: string | null
  trend: string
  history_similarity: string | null
  data_quality: string | null
  confidence: number | null
  drivers: string[]
  input_freshness: Record<string, string>
  missing_critical: boolean
  data_cut: string | null
  computed_at: string
  last_updated: string | null
  model_version: string
}

export interface Observation {
  observed_at: string
  value: number
  unit: string
  source: string
  quality: string
}

export interface LatestData {
  district: District
  variables: Record<string, Observation[]>
}

export interface HistoricalSeries {
  district: District
  series: Record<string, { date: string; value: number }[]>
  flood_events: {
    event_id: string
    glide: string | null
    name: string
    from_date: string
    to_date: string | null
    alert_level: string | null
    source: string
  }[]
}

export interface SourceStatus {
  key: string
  name: string
  provider: string
  url: string
  requires_auth: boolean
  auth_env_var: string | null
  status: string
  detail: string
  last_checked_at: string | null
  last_success_at: string | null
  last_data_at: string | null
}

export interface ModelInfo {
  model_version: string
  algorithm: string
  trained_at: string
  training_period: string
  validation_period: string
  test_period: string
  features: string[]
  metrics: Record<string, unknown>
  thresholds: {
    moderate: number
    high: number
    critical: number
    method: Record<string, string>
    validation_prevalence: number
  }
  target: string | null
  target_definitions: Record<string, string> | null
  validation_selection: Record<string, { pr_auc: number }> | null
  feature_importance: { feature: string; importance: number }[]
  calibration: { prob_true: number[]; prob_pred: number[]; brier_test: number }
  probability_meaning: string | null
  prediction_horizon: string | null
  ablation_study: {
    question: string
    target: string
    models: Record<string, { features: string[]; validation: Record<string, number>; test: Record<string, number>; note?: string }>
    interpretation: Record<string, unknown>
  } | null
}

export interface AlertItem {
  id: number
  district: string
  state: string
  alert_type: string
  risk_level: string
  probability: number | null
  message: string
  created_at: string
}

export interface Explanation {
  district: { id: number; name: string; state: string }
  probability: number | null
  probability_meaning?: string
  calibrated: boolean
  prediction_horizon?: string
  target_definition?: string
  missing_critical: boolean
  data_cut: string | null
  freshness: Record<string, string>
  data_quality?: string
  trend?: string
  uncertainty_quantified?: boolean
  uncertainty_note?: string
  signals?: {
    rain?: { level_percentile?: number | null; description?: string; rain_1d_mm?: number | null; rain_7d_mm?: number | null }
    soil?: { soil_moisture_m3m3?: number | null; relative_wetness_percentile?: number | null; description?: string; change_3d?: number | null }
    river?: {
      current_m3s?: number | null
      current_date?: string
      record_days?: number
      seasonal_mean_m3s?: number | null
      seasonal_median_m3s?: number | null
      ratio_to_seasonal_mean?: number | null
      ratio_to_seasonal_median?: number | null
      trend?: string
      trend_pct_per_day?: number | null
      record_percentile?: number | null
    } | null
    recent_high_flow?: { exceedance_30d?: number; exceedance_90d?: number; days_since_last?: number }
  }
  historical_analogues?: {
    episodes: { date: string; rain_7d_mm: number | null; soil_saturation: number | null;
                discharge_ratio_mean: number | null; similarity_pct: number }[]
    n_past_episodes: number
    n_similar_episodes: number
    share_of_similar_episodes_pct: number
    features_used: string[]
    method: string
  } | null
  model_version: string
  features: {
    feature: string
    label: string
    unit: string
    value: number | null
    hist_median?: number
    hist_p90?: number
    hist_p99?: number
    in_model: boolean
  }[]
  contributions: { feature: string; delta: number }[]
  last_flood?: {
    date: string
    days_ago: number
    kind: string
    event_name: string | null
    glide?: string | null
    matched_report?: { name: string; glide: string | null; alert_level: string | null; source: string; event_id: string } | null
    conditions_during_flood: {
      max_daily_rain_mm: number | null
      mean_soil_moisture: number | null
      max_discharge_m3s: number | null
    }
    conditions_now: {
      max_daily_rain_mm: number | null
      mean_soil_moisture: number | null
      max_discharge_m3s: number | null
    }
    note: string
  } | null
  method_notes: Record<string, string>
}

export interface FloodHistory {
  district: District
  available_from: string | null
  available_to: string | null
  threshold_value: number | null
  threshold_note: string
  episode_term: string
  flood_days: { date: string; value: number }[]
  episodes: {
    start: string; end: string; peak_m3s: number; days: number
    conditions: { max_daily_rain_mm: number | null; mean_soil_moisture: number | null; peak_discharge_m3s: number | null }
    officially_reported: boolean
    event_name: string | null; official_event_name: string | null
    glide: string | null; alert_level: string | null; name_source: string | null
  }[]
  official_events: { event_id: string; name: string; glide: string | null;
                     from_date: string; to_date: string | null;
                     alert_level: string | null; source: string }[]
  yearly: { year: number; flood_days: number; peak: number; days: number }[]
  monthly: { month: string; flood_days: number; peak: number }[]
}

export interface LocateResult {
  found: boolean
  query: string
  detail?: string
  limitation?: string
  city?: { name: string; state: string; lat: number; lon: number; matches: { name: string; state: string }[] }
  district?: { id: number; name: string; state: string }
  resolution?: { method: string; distance_km: number }
  explanation?: Explanation
}

export const RISK_COLORS: Record<string, string> = {
  CRITICAL: '#7f1d1d',
  HIGH: '#ea580c',
  MODERATE: '#eab308',
  LOW: '#16a34a',
  DATA_UNAVAILABLE: '#9ca3af',
}
