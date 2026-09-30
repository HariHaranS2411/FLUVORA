# Data Source Matrix — India Flash Flood Early Warning

**Verification date: 2026-09-24.** Every endpoint below was probed with live HTTP requests
from this environment before implementation. Results record what was *actually observed*,
not what documentation claims.

Verification method: `curl` / direct HTTP against each endpoint with real parameters
(Chennai 13.08N 80.27E, Prayagraj 25.43N 81.83E; historical windows 2015–2026).

## 1. VERIFIED LIVE — IN ACTIVE USE

### 1.1 Open-Meteo Historical Weather API (ERA5 / ERA5-Land)
- **Provider:** Open-Meteo (aggregator for Copernicus C3S / ECMWF ERA5 family)
- **URL/API:** `https://archive-api.open-meteo.com/v1/archive`
- **Variables verified:** daily `precipitation_sum` (mm), `soil_moisture_0_to_7cm_mean` (m³/m³),
  `soil_moisture_3_to_9cm_mean` (m³/m³), `et0_fao_evapotranspiration` (mm),
  `precipitation_hours` (h), `wind_speed_10m_max` (km/h)
- **Geographic resolution:** ~9 km (ERA5-Land reanalysis grid)
- **Historical coverage:** 1940 → ~5 days ago (probed 2015 and 2024 windows OK)
- **Update frequency:** daily reanalysis refresh (~5-day lag)
- **Authentication:** none required
- **License/access:** CC-BY 4.0 (attribution required — displayed in UI "Data Sources")
- **Current availability:** ✅ VERIFIED LIVE (2026-09-24, real Chennai monsoon values returned)
- **Batch:** multiple lat/lon pairs per request (probed 8 locations/call)

### 1.2 Open-Meteo Forecast / Current Weather API
- **Provider:** Open-Meteo (ECMWF IFS + NOAA GFS blends; soil moisture from ERA5-based land model)
- **URL/API:** `https://api.open-meteo.com/v1/forecast`
- **Variables verified:** `current` precipitation/rain/soil_moisture_0_to_7cm (returned with real
  timestamp `2026-09-24T13:15` + interval), `hourly` precipitation, soil moisture, `forecast_days`
- **Geographic resolution:** ~11 km global models
- **Coverage:** present + 16-day forecast
- **Authentication:** none
- **License:** CC-BY 4.0
- **Current availability:** ✅ VERIFIED LIVE (real current precipitation observed at probe time)
- **Batch:** multiple locations per request ✅

### 1.3 Open-Meteo Flood API (GloFAS v4 — Copernicus EMS / JRC)
- **Provider:** Copernicus Global Flood Awareness System (JRC) via Open-Meteo
- **URL/API:** `https://flood-api.open-meteo.com/v1/flood`
- **Variables verified:** daily `river_discharge` (m³/s), `river_discharge_max/mean`
- **Geographic resolution:** 0.05° (~5 km) GloFAS v4 river network
- **Coverage:** 1984 → present + 7 months seasonal forecast (docs verified)
- **Authentication:** none
- **License:** CC-BY 4.0 (GloFAS/Copernicus attribution displayed)
- **Current availability:** ✅ VERIFIED LIVE (real discharge 1451 m³/s at Prayagraj, July 2020)

### 1.4 Open-Meteo Elevation API (Copernicus DEM GLO-90)
- **URL/API:** `https://api.open-meteo.com/v1/elevation`
- **Variables:** elevation (m), batch of coordinates
- **Resolution:** 90 m
- **Authentication:** none — **Current availability:** ✅ VERIFIED LIVE ([10.0, 20.0, 13.0] Chennai area)

### 1.5 GDACS Event Archive (EC JRC / UN disaster alerts)
- **Provider:** Global Disaster Alert and Coordination System — European Commission JRC + UN OCHA/GC
- **URL/API:** `https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH?eventtype=FL&fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD`
- **Variables verified:** flood events with `eventid`, GLIDE id, `fromdate`/`todate`,
  `alertlevel` (Green/Orange/Red), coordinates, severity, country
- **Resolution:** event points
- **Coverage:** ~2015 → present (probed 2015–2020: 54 events returned, 25 IND)
- **Note (documented limitation):** the SEARCH endpoint returns some near-neighbour country
  events (e.g. IDN/BGD) and ignores `country=`/`limit=` params; **client-side filtering by
  `iso3 == "IND"` and date window is implemented** in the pipeline.
- **Authentication:** none — **License:** JRC/UN open reuse with attribution
- **Current availability:** ✅ VERIFIED LIVE (real Aug–Sep 2026 India floods returned)

### 1.6 GeoBoundaries India ADM2 (District boundaries)
- **Provider:** geoBoundaries (William & Mary geoLab), sourced from lgdirectory.gov.in
  (Government of India, via Pathways Data Pvt. Ltd.)
- **URL/API:** `https://www.geoboundaries.org/api/current/gbOpen/IND/ADM2/` → simplified GeoJSON
  at `github.com/wmgeolab/geoBoundaries/.../geoBoundaries-IND-ADM2_simplified.geojson`
- **Verified:** **735 district features**, properties: shapeName, shapeISO, shapeID
- **License:** ODbL 1.0 — **Current availability:** ✅ DOWNLOADED (data/raw/, 7.98 MB)
- **ADM1 (states):** same API, ADM1 tier — ✅ available

## 2. VERIFIED — REQUIRES REGISTRATION (documented, integration interface provided)

### 2.1 NASA POWER API (MERRA-2)
- **URL/API:** `https://power.larc.nasa.gov/api/temporal/daily/point?parameters=PRECTOTCORR&...`
- **Verified live:** ✅ real MERRA2 daily precipitation returned (Chennai Jan 2021:
  `{"20210105": 49.85, "20210106": 40.12, ...}`)
- **Status in app:** **secondary/backup rainfall source**; free community key recommended for
  higher rate limits → `NASA_POWER_API_KEY` in `.env` (obtain at
  https://power.larc.nasa.gov/ — Registration). Works without key at low volume.

### 2.2 ReliefWeb Disasters API (UN OCHA)
- **URL/API:** `https://api.reliefweb.int/v2/disasters` (POST JSON; v1 is decommissioned → HTTP 410)
- **Verified:** API reachable; requires **approved appname** → HTTP 403
  "You are not using an approved appname. Kindly request an appname from ReliefWeb"
- **Status in app:** supplementary historical event source; disabled by default. Register an
  appname at https://apidoc.reliefweb.int and set `RELIEFWEB_APPNAME=` to enable.

## 3. VERIFIED — NOT USABLE FOR THIS SYSTEM (kept for transparency)

| Source | Probe result | Reason |
|---|---|---|
| Global Flood Database (cloudtostreet.ai) | Site 200 OK; no REST API found in app bundle (Earth-Engine-backed) | Data accessible only via manual Google Earth Engine export; documented as such |
| Dartmouth Flood Observatory archive | floodobservatory.colorado.edu/Events.html → HTTP 404 | Legacy archive retired |
| IMD mausam.imd.gov.in | HTTP 200 (website) | No public machine API/feed for district rainfall that can be legally verified; IMD publishes gridded data as manual downloads. **Not integrating rather than scraping/faking** |
| CWC cwc.gov.in | HTTP 200 (website) | Flood-forecast bulletins are PDF/site pages; India-WRIS portal (indiawris.gov.in) unreachable from this environment (connection timeout). No verifiable open API → river level uses GloFAS (real Copernicus data) instead |
| data.gov.in | HTTP 200 (website) | Dataset APIs require per-dataset API keys + many datasets show stale "sample data" in open tier; not fabricating access. Hook + `DATA_GOV_IN_API_KEY` provided if the operator registers |

## 4. IMPLICATIONS FOR THE ML DESIGN (honest scoping)

- **Rainfall features:** ERA5 reanalysis (real) — 1h/3h/6h/12h/24h/3d/7d accumulations,
  intensity, acceleration. ✅
- **Soil features:** ERA5-Land volumetric soil moisture (real), saturation & change. ✅
- **Hydrology features:** GloFAS river discharge + return-period exceedance (real). ✅
- **Terrain features:** Copernicus DEM 90 m elevation via API (real); **slope is computed from
  real elevation**, not invented. ✅
- **Flood labels:** GDACS India flood events 2015→present (real, JRC/UN). Events are point/
  polygon-anchored → labels are **district×day "flood report" flags**, not instrument-grade
  flood extents. This is stated everywhere the model is shown. ⚠️ documented label limitation
- **What the model predicts:** P(flood reported in district within 0–3 days | environmental
  conditions) — a real, traceable classification target.
- **Indian gov sources:** IMD/CWC/GSI have no verifiable open APIs from this environment;
  they appear in the Data Sources page with real connection-check status ("Unreachable /
  No public API — using Copernicus/NOAA equivalents"). No fabricated Indian government data.
- **data.gov.in / ReliefWeb / NASA POWER:** credential-gated integrations stubbed behind
  environment variables with real setup instructions; the UI shows their true status.
