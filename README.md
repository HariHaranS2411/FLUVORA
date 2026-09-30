# FLUVORA — India Flash Flood Early Warning & Prediction Platform

We are **TEAM_KRYPTON**, and here is our project.
**FLUVORA** is a real-data-only, decision-support prototype for India-wide flash-flood **risk
prediction** and **location-based early warning**. It contains **no mock data**:
every number shown in the UI comes from a verified public source or from a model
trained on that data, and missing data is displayed as *unavailable* rather than
fabricated.

> **This is decision support, not an official warning.** Predictions are
> probabilistic. Always follow IMD / CWC / NDMA and local administration
> instructions.

## Verified real data sources

All sources were probed live before implementation (see
[DATA_SOURCE_MATRIX.md](DATA_SOURCE_MATRIX.md) for the full matrix with
license/resolution/coverage and observed results):

| Purpose | Source | Key needed |
|---|---|---|
| Historical rainfall + soil moisture | Copernicus/ECMWF **ERA5** via Open-Meteo Archive API | No |
| Current conditions | Open-Meteo Forecast API (ECMWF/NOAA blends) | No |
| River discharge (history + now) | Copernicus **GloFAS v4** via Open-Meteo Flood API | No |
| Elevation / slope | Copernicus **DEM GLO-90** via Open-Meteo Elevation API | No |
| Historical flood events | **GDACS** archive (EC JRC / UN) | No |
| District boundaries (735) | **geoBoundaries** ADM2 (source: lgdirectory.gov.in) | No |
| Optional backups | NASA POWER (free key), ReliefWeb (appname), **data.gov.in** (free key + resource IDs, real dataset ingestion) | Yes (see .env.example) |
| Not integrated (no verifiable open API) | IMD, CWC, India-WRIS, GSI | — |

## What the ML model actually is

**The deployed model estimates the probability that a district's river
discharge crosses its modeled high-flow threshold (GloFAS causal 5-year
rolling P99) today. It does NOT output a probability of an officially
observed flood**, and the UI never presents it that way.

- **Target** (selected on validation between two real definitions):
  - `y`: GDACS flood report for the district within `[t, t+3]`
  - `y_hydro`: GloFAS discharge ≥ the district's *causal* 5-year rolling 99th percentile (**selected**)
- **Features** (all real): rainfall 1/2/3/5/7-day sums, rainfall change &
  acceleration, recent-vs-previous week, antecedent precipitation index,
  3-day rain hours, soil moisture (0–7 cm) + saturation + 3-day change,
  discharge + seasonal ratios, elevation, slope, month, causal historical
  flood/high-flow memory.
- **Prediction horizon**: 0–24 h nowcast (features use observations up to the
  data cut). Short-term rain forecasts are not ingested; there is no 24/72 h
  forecast signal yet, and the UI says so.
- **Time-based splits** (no leakage): train 2020-09..2023-08 · validation
  2023-09..2024-08 · test 2024-09..2025-08.
- Candidates (logistic regression, histogram gradient boosting) compete on
  **validation PR-AUC**; probabilities are isotonic-calibrated on validation;
  **LOW/MODERATE/HIGH/CRITICAL thresholds are derived on validation** with
  documented rules (lift over prevalence, F2-max, precision target).
- Explainability: permutation importance + per-prediction ablation
  sensitivities; calibration curve; full metrics on the untouched test split.

### Persistence ablation (`ml/evaluation/ablation_baselines.py`)

Because discharge is persistent, the model could look good by merely learning
"today's discharge predicts tomorrow's". `python -m ml.evaluation.ablation_baselines`
trains feature-group models on the SAME protocol and evaluates a no-ML
persistence rule; the result is written to `data/processed/ablation_report.json`
and served via `/api/model-info` → the Methodology page. Computed on the real
dataset (no fabricated numbers): full model test PR-AUC **0.6079** vs
discharge-only **0.3017** and rain+soil-only **0.1812**; the no-ML persistence
rule scores PR-AUC **0.397** with recall **0.627**. The model adds real value
beyond persistence, but part of its skill remains persistence — one of the
documented limitations.

### Risk pipeline

```
REAL DATA → DATA QUALITY/VALIDATION → FEATURE ENGINEERING
→ RAINFALL SIGNAL → HYDROLOGICAL SIGNAL → HISTORICAL CONTEXT
→ RISK FUSION (model) → RISK LEVEL → EXPLANATION → ALERT
```

Each component has a single owner: `app/services/signals.py` (discharge
condition, high-flow memory, historical analogues), `app/services/risk_engine.py`
(features, model, explanation), `app/alerts/engine.py` (hysteresis + cooldown
+ dedup; never alerts on insufficient data). Data quality, model estimate and
uncertainty are reported as separate fields — never merged into one number.

## Repository layout

```
flash-flood-ai/
├── backend/    FastAPI app: API, risk engine, alerts, source status, DB models
├── frontend/   React+TS+Vite+Tailwind UI: Leaflet maps, Recharts, minimalist FLUVORA design
├── ml/         data_processing / feature_engineering / training / evaluation
├── data/       raw (downloads) → processed (pickles, datasets, model card)
├── database/   migrations placeholder (SQLite-compatible schema via SQLAlchemy)
├── docker/     Dockerfiles + nginx
└── docker-compose.yml   (PostGIS + backend + frontend)
```

## Setup (local, no Docker)

```bash
# 1. Python deps
pip install -r backend/requirements.txt

# 2. Real boundaries (downloads verified GeoBoundaries files)
curl -L -o data/raw/geoBoundaries-IND-ADM2_simplified.geojson \
  https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IND/ADM2/geoBoundaries-IND-ADM2_simplified.geojson
python -m ml.data_processing.prepare_districts        # centroids + states (735)

# 3. Real history (ERA5 + GloFAS, resumable; ~40 API calls per pass; watch quotas)
python -m ml.data_processing.ingest_history --start 2020-09-01 --end 2025-08-31 --only archive
python -m ml.data_processing.ingest_history --start 2020-09-01 --end 2025-08-31 --only flood

# 4. Real events + labels
python -m ml.data_processing.ingest_events            # GDACS 2015-2026, matched to districts

# 5. Dataset + training (time-based splits, metrics, thresholds)
python -m ml.feature_engineering.build_dataset
python -m ml.training.train_model

# 6. DB + API
python -m app.seed_db                                 # districts, events, model registry
python -m app.services.backfill_db                    # observations from real history
uvicorn app.main:app --reload --app-dir backend       # from repo root: uvicorn backend.app.main:app
```

Frontend:

```bash
cd frontend && npm install && npm run dev             # http://localhost:5173
```

The backend computes current risk for all districts every 30 minutes
(`refresh_interval_seconds` in `backend/app/config.py`), using the latest real
forecast/current API values, generates deduplicated alerts, and exposes:

```
GET /api/locate?q=<city>     city lookup: real geocoding -> district -> risk + explanation
GET /api/india/risk          GET /api/risk/{district_id}     GET /api/explain/{district_id}
GET /api/map-data            GET /api/latest-data/{district_id}
GET /api/historical/{id}     GET /api/flood-history/{district_id}
GET /api/states              GET /api/districts?state=
GET /api/geo/districts       GET /api/geo/states             GET /api/alerts
GET /api/notify/status       POST /api/notify/subscribe      POST /api/notify/test
GET /api/data-sources        POST /api/data-sources/check
POST /api/data-gov-in/ingest (key-gated)                     GET /api/model-info
```

City resolution uses the Open-Meteo Geocoding API (verified live), filters to
India, and maps the city to a district by point-in-polygon on the real ADM2
geometry (admin2-name and nearest-centroid fallbacks, with the distance
reported). The "Check My City" page shows the verdict and the full computation
on the same page.

## How a prediction is explained in the UI

Every location page has a **"How this prediction was computed"** panel:
1. the real observations used (with timestamps),
2. the exact feature values vs their historical median/90th/99th percentiles,
3. per-feature ablation contributions ("discharge vs seasonal mean raised the
   probability by 5%"),
4. the documented thresholds that map probability to risk level.

## Browser notifications for HIGH risk (no accounts, no subscription)

The only user-facing action is **🔔 Enable Notifications** on the Alerts page;
after permission is granted it shows **✅ Notifications enabled**. There is no
account, email, SMS, or subscription concept anywhere in the UI — the browser's
internal push registration is an implementation detail stored anonymously
(`browser_notify_endpoints`: endpoint + keys + timestamps only).

Flow: existing risk engine → existing alert engine → a genuinely NEW HIGH/CRITICAL
alert row (deduplicated by `event_key` + 12 h cooldown; refreshed rows and unchanged
conditions never notify; a real downgrade resolves the alert so a later re-escalation
notifies again) → Web Push to every active browser endpoint → service worker
(`frontend/public/service-worker.js`) displays the notification even when the site
is closed; clicking it opens the district's page.

Setup:
1. `pip install pywebpush py_vapid`
2. Generate keys (server-side only, never commit):
   `python -c "from app.services.webpush import generate_vapid_keys; print(generate_vapid_keys())"`
3. Put `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` (a `mailto:`)
   in `.env` at the repo root (already loaded by `app.config`).
4. Without keys or `pywebpush`, notifications are simply skipped — alerting and
   prediction continue unaffected (failures are logged, never raised).

Browsers require HTTPS (or localhost) for push; iOS Safari needs the site added
to the home screen first. A user who blocks the permission prompt must re-enable
from browser settings — the code never re-prompts after a denial.

## Deployment

`docker compose up --build` starts PostGIS + backend + frontend. Point
`DATABASE_URL` at any PostgreSQL/PostGIS instance; the schema is ORM-managed and
SQLite-compatible for local runs. Scheduled refresh runs inside the backend
(APScheduler); for serverless platforms replace it with their cron/worker.

## Frontend

The UI is a minimalist dark dashboard (Inter, sky accent) built with React +
TypeScript + Vite + Tailwind, with route-level code splitting so the first
paint does not wait for the Leaflet/Recharts chunks.

- **Overview** — live national risk counts, animated stat cards, the risk map
  (bright state borders + dark casing; state mode shows name labels).
- **Check My City** — type a city, get its district's real verdict + explanation.
- **Live Risk Map** — state/district toggle, hover readouts, click-through.
- **Current Conditions** — sortable table; expand a row for the model's
  ablation chart (animated gradient bars per feature contribution).
- **Historical Analysis** — GloFAS high-flow episodes vs official GDACS events,
  per-year animated charts (days/month, peak discharge, flood-day discharge).
- **Location** — full district page: rainfall/discharge history (glow line +
  gradient area), current conditions, last-flood comparison, full calculation.
- **Alerts** — 🔔 Enable Notifications (browser Web Push, no accounts), device
  count, test push, and the live alert feed.

### Performance notes (measured)

- Map geometry endpoints serve **pre-serialized, pre-gzipped** bytes from a
  cache keyed by the risk-data signature (`Content-Encoding: gzip`,
  `Cache-Control: max-age=300`); the payloads are also **warmed during backend
  startup**, so even the first view after a restart is fast. District payload:
  ~2.1 MB gzipped (from 6.5 MB raw); repeat browser loads come from cache.
- `/api/india/risk` uses a grouped-MAX query over a composite index
  (`computed_at`, `district_id`); `risk_assessments` is pruned to the 30 most
  recent runs; SQLite runs in WAL mode. Steady-state latencies: india/risk
  ~0.6 s, map-data ~1.3 s, geo/states ~0.03 s, alerts <0.05 s.
- Initial JS bundle ~376 kB (118 kB gzip); recharts/leaflet chunks load on
  demand per route.

## Known limitations (by design, not hidden)

- GDACS India flood reports are sparse (~19 events 2015–2026). Labels are
  imperfect; the hydrological target mitigates but does not equal confirmed
  ground truth. The Model Performance page shows the true test metrics —
  including the confusing-matrix cost of misses and false alarms.
- Gridded reanalysis/model data ≠ rain gauges; values are district-centroid
  samples at ~5–11 km resolution. District is the honest resolution limit.
- If a source is down, the UI shows **DATA UNAVAILABLE** for affected fields and
  withholds predictions that would require the missing critical inputs.
- IMD/CWC/GSI data are not integrated (no verifiable open APIs); the Data
  Sources page shows their real probe status instead of pretending.
