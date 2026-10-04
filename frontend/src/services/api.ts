import type {
  AlertItem, Assessment, District, Explanation, FloodHistory, HistoricalSeries,
  LatestData, LocateResult, ModelInfo, RiskMap,
} from '../types'

// FloodHistory and LocateResult are defined once in types/index.ts and
// re-exported here so existing imports keep working (single source of truth).
export type { FloodHistory, LocateResult } from '../types'

// Production (Render): VITE_API_BASE is baked in at build time and points at the
// backend service, e.g. https://fluvora-api.onrender.com. Unset in dev, where
// Vite's /api proxy (vite.config.ts) forwards to the local backend. Exported so
// call sites outside this module (map geojson, notifications) hit the same base.
export const API_BASE = import.meta.env.VITE_API_BASE ?? ''

const BASE = `${API_BASE}/api`

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`)
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`)
  return r.json() as Promise<T>
}

export const api = {
  locate: (q: string) => get<LocateResult>(`/locate?q=${encodeURIComponent(q)}`),
  floodHistory: (id: number) => get<FloodHistory>(`/flood-history/${id}`),
  riskMap: () => get<RiskMap>('/india/risk'),
  risk: (id: number) => get<{ district: District; assessment: Assessment | null; note?: string }>(`/risk/${id}`),
  explain: (id: number) => get<Explanation>(`/explain/${id}`),
  latestData: (id: number) => get<LatestData>(`/latest-data/${id}`),
  historical: (id: number, days = 365) => get<HistoricalSeries>(`/historical/${id}?days=${days}`),
  districts: (state?: string) =>
    get<District[]>(`/districts${state ? `?state=${encodeURIComponent(state)}` : ''}`),
  states: () => get<{ state: string; districts: number }[]>('/states'),
  alerts: (limit = 100) => get<AlertItem[]>(`/alerts?limit=${limit}`),
  notifyStatus: () => get<{ configured: boolean; active_browsers: number }>('/notify/status'),
  modelInfo: () => get<ModelInfo>('/model-info'),
}
