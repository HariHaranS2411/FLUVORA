"""FastAPI application (spec §24). All returned values are real (DB-stored
observations/model outputs). Missing data is returned as explicit nulls /
"data unavailable" markers — never fabricated."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta

import pandas as pd
import gzip

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import BASE_DIR, settings
from .database import engine, get_db
from .models import (
    Alert, Base, DataSourceStatus, District, FloodEvent, ModelInfo, Observation,
    RiskAssessment, Subscription,
)
from .schemas import (
    AlertOut, DistrictOut, ModelInfoOut, RiskMapOut, SourceStatusOut,
    SubscribeIn, SubscriptionOut,
)
from .services.signals import discharge_daily, hydro_memory

STATE_LABELS = {
    "LOW": "No high-flow signals",
    "MODERATE": "Some high-flow signals",
    "HIGH": "Strong high-flow signals",
    "CRITICAL": "Severe high-flow signals",
    "DATA_UNAVAILABLE": "Insufficient data",
}
PROBABILITY_MEANING = (
    "Model-estimated probability of crossing the district's modeled high-flow "
    "threshold (hydrological), not a probability of an officially observed flood.")

_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from .services.risk_engine import refresh_all_risk
    from .services.source_status import check_all
    from .alerts.engine import generate_alerts

    Base.metadata.create_all(engine)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(refresh_job, "interval", seconds=settings.refresh_interval_seconds)
    scheduler.start()
    _state["scheduler"] = scheduler

    # Warm the heavy caches in the background (model artifact + 206MB dataset
    # pickle used by historical analogues). Without this, the FIRST explain
    # requests after a restart block on cold loads and look "hung"; with it,
    # every district — including data-unavailable islands — answers in <1s.
    import asyncio

    async def _warm():
        try:
            await asyncio.to_thread(_warm_caches)
            print("[startup] model + historical-dataset caches warm")
        except Exception as e:  # noqa: BLE001
            print(f"[startup] cache warming failed (non-fatal): {e}")

    # Pre-serialize + pre-gzip the big map payloads so the FIRST map view
    # after a restart is fast too (they only change when a risk cycle lands).
    async def _warm_map_payloads():
        import gzip as _gzip
        import json as _json

        def _build():
            try:
                from .database import SessionLocal
                db = SessionLocal()
                try:
                    by_id = list(_latest_assessments(db))
                    sig = (max(r.computed_at for r in by_id).isoformat(),) if by_id else "none"
                    for level in ("district", "state"):
                        gj = _build_map_geojson(level, by_id, {d.id: d for d in db.query(District).all()})
                        raw = _json.dumps(gj, separators=(",", ":")).encode("utf-8")
                        _map_bytes_cache[f"{level}|{sig}|{level}"] = _gzip.compress(raw, compresslevel=6)
                    _geo_bytes_response(_states_geo(), "geo-states|static")
                    print("[startup] map payload caches warm")
                finally:
                    db.close()
            except Exception as e:  # noqa: BLE001
                print(f"[startup] map payload warming failed (non-fatal): {e}")

        await asyncio.to_thread(_build)

    asyncio.get_running_loop().create_task(_warm())
    asyncio.get_running_loop().create_task(_warm_map_payloads())

    # First-run / ephemeral-filesystem bootstrap (Render free tier): every
    # spin-up starts with an empty database, so seed districts, flood events
    # and the model registry, then run one refresh cycle immediately instead
    # of waiting 30 minutes for the first scheduled one. No-op whenever data
    # already exists (normal local dev, paid-tier persistent disk).
    async def _bootstrap_if_empty():
        from .database import SessionLocal

        db = SessionLocal()
        try:
            has_districts = db.query(District.id).first() is not None
            has_scores = db.query(RiskAssessment.id).first() is not None
        finally:
            db.close()
        if has_districts and has_scores:
            return
        try:
            if not has_districts:
                from .seed_db import seed
                await asyncio.to_thread(seed)
        except Exception as e:  # noqa: BLE001
            print(f"[startup] auto-seed failed (non-fatal): {e}")
            return
        try:
            # The first cycle can come back empty when the upstream forecast
            # API throttles a fresh IP (429): a couple of quick retries here
            # keep first visitors from waiting 30 minutes for real numbers.
            for attempt in range(1, 4):
                if attempt > 1:
                    await asyncio.sleep(60)
                    await refresh_job()
                db = SessionLocal()
                try:
                    usable = (db.query(func.count(RiskAssessment.id))
                              .filter(RiskAssessment.probability.isnot(None))
                              .scalar() or 0)
                    total = db.query(func.count(District.id)).scalar() or 0
                finally:
                    db.close()
                if total and usable * 10 >= total:
                    print(f"[startup] bootstrap refresh complete ({usable}/{total} districts with real risk)")
                    break
                if attempt < 3:
                    print(f"[startup] only {usable}/{total} districts scored after cycle {attempt}; retrying")
            else:
                print("[startup] bootstrap finished with few usable districts; scheduler keeps retrying")
        except Exception as e:  # noqa: BLE001
            print(f"[startup] bootstrap refresh failed (non-fatal): {e}")

    asyncio.get_running_loop().create_task(_bootstrap_if_empty())
    yield
    scheduler.shutdown()


async def refresh_job() -> None:
    """Scheduled data refresh. The CPU-heavy scoring and bulk DB writes run in a
    worker thread so the event loop keeps serving API requests during the cycle
    (previously the whole API froze for the ~1-2 min a cycle takes)."""
    import asyncio

    from .database import SessionLocal
    from .services.risk_engine import refresh_all_risk
    from .alerts.engine import generate_alerts

    def _cycle_sync() -> int:
        db = SessionLocal()
        try:
            run_id = datetime.utcnow()
            n = asyncio.run(refresh_all_risk(db))
            generate_alerts(db, run_id=run_id)
            return n
        finally:
            db.close()

    try:
        n = await asyncio.to_thread(_cycle_sync)
        print(f"[refresh] scored {n} districts")
        await asyncio.to_thread(_prune_old_assessments)
    except Exception as e:  # noqa: BLE001
        print(f"[refresh] cycle failed (non-fatal): {e}")


def _prune_old_assessments() -> None:
    """Keep the risk_assessments table bounded: retain the 30 most recent runs
    (~15 h of history at the 30-min cadence — more lives in observations and
    flood-history). Without this the table grows 735 rows/cycle forever and
    every risk/map request slows down."""
    from sqlalchemy import text

    from .database import SessionLocal

    db = SessionLocal()
    try:
        cutoff = db.execute(
            text(
                "SELECT computed_at FROM risk_assessments "
                "GROUP BY computed_at ORDER BY computed_at DESC LIMIT 1 OFFSET 29"
            )
        ).scalar()
        if cutoff is not None:
            db.execute(
                text("DELETE FROM risk_assessments WHERE computed_at < :c"), {"c": cutoff}
            )
            db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        print(f"assessment prune failed (non-fatal): {e}")
    finally:
        db.close()


def _warm_caches() -> None:
    """Touch the lazily-loaded heavy artifacts once so first requests are fast."""
    from .services.risk_engine import _load
    from .services import signals

    _load()
    if (signals.PROCESSED / "dataset.pkl").exists():
        signals._episode_frame()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Compress big JSON responses (map geometry is ~16 MB raw across endpoints;
# gzip shrinks it ~8x, which is what actually dominates map page load time).
# Level 6: JSON compresses nearly as well as level 9 but ~3x faster per request.
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)


@app.get("/api/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


_geo_cache: dict | None = None
_states_geo_cache: dict | None = None
_geo_text_cache: dict[str, str] = {}
_map_bytes_cache: dict[str, bytes] = {}
_district_shape_order_cache: list[str] | None = None


def _geo_text(fname: str) -> str:
    """Cached geojson file text — parsing multi-MB geometry per request wasted
    hundreds of ms on every map load."""
    path = BASE_DIR / "data" / "raw" / fname if fname.startswith("geoBoundaries") \
        else BASE_DIR / "data" / "processed" / fname
    if fname not in _geo_text_cache:
        _geo_text_cache[fname] = path.read_text(encoding="utf-8")
    return _geo_text_cache[fname]


def _district_shape_order() -> list[str]:
    """Cached shape_id order from districts.csv (used to map polygons -> ids)."""
    global _district_shape_order_cache
    if _district_shape_order_cache is None:
        import csv as _csv
        order: list[str] = []
        with open(BASE_DIR / "data" / "processed" / "districts.csv", encoding="utf-8") as fh:
            for row in _csv.DictReader(fh):
                order.append(row["shape_id"])
        _district_shape_order_cache = order
    return _district_shape_order_cache


def _states_geo() -> dict:
    """Load (once) the real State/UT polygons (geoBoundaries ADM1)."""
    global _states_geo_cache
    if _states_geo_cache is None:
        path = BASE_DIR / "data" / "raw" / "geoBoundaries-IND-ADM1_simplified.geojson"
        if not path.exists():
            raise HTTPException(503, "State geometry not prepared yet.")
        _states_geo_cache = json.loads(path.read_text(encoding="utf-8"))
    return _states_geo_cache


def _geo_bytes_response(gj: dict, cache_key: str) -> Response:
    """Serialize AND gzip the map payload once per risk-data state, then reuse
    the exact compressed bytes afterwards. json.dumps of the 7 MB geometry
    costs ~1.8 s and gzipping it ~1 s per request; the cache key (risk
    signature) changes only when a refresh cycle produces new assessments, so
    data is never stale beyond that. Serving pre-compressed bytes with
    Content-Encoding set makes Starlette's GZipMiddleware skip re-compression
    (verified: middleware checks for an existing content-encoding header)."""
    data = _map_bytes_cache.get(cache_key)
    if data is None:
        raw = json.dumps(gj, separators=(",", ":")).encode("utf-8")
        data = gzip.compress(raw, compresslevel=6)
        if len(_map_bytes_cache) >= 4:
            _map_bytes_cache.clear()
        _map_bytes_cache[cache_key] = data
    return Response(
        content=data,
        media_type="application/json",
        headers={
            "Content-Encoding": "gzip",
            # Geometry only changes with the risk cycle; let browsers reuse it.
            "Cache-Control": "public, max-age=300",
        },
    )


@app.get("/api/geo/districts")
def geo_districts():
    """Real district polygons (geoBoundaries ADM2, simplified) for the map."""
    global _geo_cache
    if _geo_cache is None:
        path = BASE_DIR / "data" / "processed" / "districts_simplified.geojson"
        if not path.exists():
            raise HTTPException(503, "District geometry not prepared yet. "
                                     "Run ml/data_processing/prepare_districts.py")
        _geo_cache = json.loads(path.read_text(encoding="utf-8"))
    return _geo_cache


@app.get("/api/geo/states")
def geo_states():
    """Real State/UT polygons (geoBoundaries ADM1) for the state overlay.
    Static geometry — served from the pre-compressed bytes cache."""
    return _geo_bytes_response(_states_geo(), "geo-states|static")


# ---------- geography ----------
@app.get("/api/states")
def states(db: Session = Depends(get_db)):
    rows = db.query(District.state, func.count(District.id)).group_by(District.state).all()
    return [{"state": s, "districts": c} for s, c in sorted(rows)]


@app.get("/api/districts")
def list_districts(state: str | None = None, db: Session = Depends(get_db)):
    q = db.query(District)
    if state:
        q = q.filter(District.state == state)
    return [
        DistrictOut(id=d.id, name=d.name, state=d.state, lat=d.lat, lon=d.lon)
        for d in q.order_by(District.state, District.name).all()
    ]


# ---------- risk ----------
def _latest_assessments(db: Session) -> list[RiskAssessment]:
    """Most recent assessment per district, preferring the newest one that has a
    real probability. (A transient upstream outage produces DATA_UNAVAILABLE rows
    for everyone; the last usable assessment with its true data_cut is more
    informative and equally honest — the UI displays its age.)
    Two grouped MAX queries over the (district_id, computed_at) unique index —
    ~1,470 rows instead of scanning the whole table into ORM objects."""
    from sqlalchemy import and_, func

    def _latest_per_district(prob_not_null: bool) -> dict[int, RiskAssessment]:
        q = db.query(
            RiskAssessment.district_id,
            func.max(RiskAssessment.computed_at).label("mx"),
        )
        if prob_not_null:
            q = q.filter(RiskAssessment.probability.isnot(None))
        sub = q.group_by(RiskAssessment.district_id).subquery()
        rows = (
            db.query(RiskAssessment)
            .join(sub, and_(
                RiskAssessment.district_id == sub.c.district_id,
                RiskAssessment.computed_at == sub.c.mx,
            ))
            .all()
        )
        return {r.district_id: r for r in rows}

    usable = _latest_per_district(prob_not_null=True)
    latest_all = _latest_per_district(prob_not_null=False)
    return [usable.get(did, r) for did, r in latest_all.items()]


@app.get("/api/india/risk")
def india_risk(db: Session = Depends(get_db)) -> RiskMapOut:
    latest = _latest_assessments(db)
    if not latest:
        return RiskMapOut(computed_at=None, districts=[], counts=None)
    dmap = {d.id: d for d in db.query(District).all()}
    counts = {"critical": 0, "high": 0, "moderate": 0, "low": 0, "data_unavailable": 0}
    items = []
    for ra in latest:
        d = dmap.get(ra.district_id)
        if d is None:
            continue
        key = (ra.risk_level or "DATA_UNAVAILABLE").lower()
        counts[key if key in counts else "data_unavailable"] += 1
        items.append({
            "district_id": d.id, "name": d.name, "state": d.state,
            "lat": d.lat, "lon": d.lon,
            "probability": ra.probability,
            "risk_probability": ra.probability,
            "risk_level": ra.risk_level,
            "state_label": STATE_LABELS.get(ra.risk_level or "DATA_UNAVAILABLE",
                                             "Insufficient data"),
            "prediction_horizon": "0–24 h (nowcast)",
            "probability_meaning": PROBABILITY_MEANING,
            "data_quality": ("good" if ra.missing_critical is False and
                             all(v == "fresh" for v in
                                 (json.loads(ra.input_freshness) if ra.input_freshness else {}).values())
                             else "fair" if not ra.missing_critical else "insufficient data"),
            "trend": ra.trend, "confidence": ra.confidence,
            "data_cut": ra.data_cut.isoformat() if ra.data_cut else None,
            "last_updated": ra.computed_at.isoformat(),
            "missing_critical": ra.missing_critical,
        })
    computed = max(r.computed_at for r in latest)
    return RiskMapOut(computed_at=computed.isoformat(), districts=items, counts=counts)


@app.get("/api/risk/{district_id}")
def risk_for(district_id: int, db: Session = Depends(get_db)):
    d = db.get(District, district_id)
    if not d:
        raise HTTPException(404, "district not found")
    ra = (
        db.query(RiskAssessment)
        .filter(RiskAssessment.district_id == district_id)
        .order_by(RiskAssessment.computed_at.desc())
        .first()
    )
    if not ra:
        return {"district": DistrictOut(id=d.id, name=d.name, state=d.state, lat=d.lat, lon=d.lon),
                "assessment": None,
                "note": "No risk assessment computed yet. The scheduler computes every "
                        f"{settings.refresh_interval_seconds}s once the model is trained."}
    return {
        "district": DistrictOut(id=d.id, name=d.name, state=d.state, lat=d.lat, lon=d.lon),
        "assessment": {
            "probability": ra.probability,
            "risk_probability": ra.probability,
            "risk_level": ra.risk_level,
            "state_label": STATE_LABELS.get(ra.risk_level or "DATA_UNAVAILABLE",
                                            "Insufficient data"),
            "probability_meaning": PROBABILITY_MEANING,
            "prediction_horizon": "0–24 h (nowcast of today's high-flow state)",
            "trend": ra.trend,
            "history_similarity": ra.history_similarity,
            "data_quality": ("good" if all(v == "fresh" for v in
                                           (json.loads(ra.input_freshness) if ra.input_freshness else {}).values())
                             else "fair"),
            "confidence": ra.confidence,
            "drivers": json.loads(ra.drivers) if ra.drivers else [],
            "input_freshness": json.loads(ra.input_freshness) if ra.input_freshness else {},
            "missing_critical": ra.missing_critical,
            "data_cut": ra.data_cut.isoformat() if ra.data_cut else None,
            "computed_at": ra.computed_at.isoformat(),
            "last_updated": ra.computed_at.isoformat(),
            "model_version": ra.model_version,
        },
    }


@app.get("/api/locate")
async def locate(q: str, db: Session = Depends(get_db)):
    """City-level lookup: real geocoding -> district -> current risk + full
    explanation, in one payload for the search page."""
    from .services.locator import locate_city

    q = q.strip()
    if not q:
        raise HTTPException(400, "provide a city or district name")
    try:
        result = await locate_city(db, q)
    except FileNotFoundError:
        raise HTTPException(503, "District geometry not prepared.")
    result["limitation"] = (
        "Risk is assessed at DISTRICT level using district-average data. City-level or "
        "neighborhood-level hydrological predictions are not currently available. If "
        "several districts correspond to an urban area, the resolution method and any "
        "distance are reported explicitly rather than silently choosing one.")
    return result


@app.get("/api/explain/{district_id}")
def explain(district_id: int, db: Session = Depends(get_db)):
    """Full 'how was this probability computed' view: real feature values,
    historical percentiles, and per-feature contributions (ablation)."""
    from .services.risk_engine import explain_district

    d = db.get(District, district_id)
    if not d:
        raise HTTPException(404, "district not found")
    try:
        return explain_district(db, d)
    except FileNotFoundError:
        raise HTTPException(503, "Model artifact not available; train the model first.")


@app.get("/api/map-data")
def map_data(db: Session = Depends(get_db), level: str = "district"):
    """Risk + geometry in one payload for the map.
    level=district (default): ADM2 polygons colored by district risk.
    level=state: ADM1 polygons aggregated from REAL district assessments
    (risk_level = most severe district level in the state, plus real counts
    and the highest district estimate). No values are invented."""
    by_id = {r.district_id: r for r in _latest_assessments(db)}
    dmap = {d.id: d for d in db.query(District).all()}
    sig = (max(r.computed_at for r in by_id.values()).isoformat(),) if by_id else "none"
    gj = _build_map_geojson(level, list(by_id.values()), dmap)
    return _geo_bytes_response(gj, f"{level}|{sig}|{level}")


_ROUND_CACHE: dict[str, str] = {}


def _round_coord(c: float) -> float:
    """Round to 3 decimals (~110 m at India's latitude) — plenty for a
    district-level choropleth and it visibly shrinks the JSON payload."""
    return round(c, 3)


def _build_map_geojson(level: str, assessments: list, dmap: dict) -> dict:
    """Build the risk-colored GeoJSON for `level` from real assessments.
    Shared by the endpoint and the startup cache warmer."""
    by_id = {r.district_id: r for r in assessments}

    if level == "state":
        path = BASE_DIR / "data" / "raw" / "geoBoundaries-IND-ADM1_simplified.geojson"
        if not path.exists():
            raise HTTPException(503, "State geometry not prepared yet.")
        gj = json.loads(_geo_text("geoBoundaries-IND-ADM1_simplified.geojson"))
        # aggregate real district assessments by state name
        agg: dict[str, dict] = {}
        for ra in by_id.values():
            d = dmap.get(ra.district_id)
            if not d:
                continue
            a = agg.setdefault(d.state, {
                "critical": 0, "high": 0, "moderate": 0, "low": 0,
                "data_unavailable": 0, "total": 0,
                "max_probability": None, "worst_district": None,
            })
            a["total"] += 1
            key = (ra.risk_level or "DATA_UNAVAILABLE").lower()
            a[key if key in a else "data_unavailable"] += 1
            if ra.probability is not None and (
                    a["max_probability"] is None or ra.probability > a["max_probability"]):
                a["max_probability"] = ra.probability
                a["worst_district"] = d.name
        severity = ["CRITICAL", "HIGH", "MODERATE", "LOW"]
        for ft in gj["features"]:
            name = ft["properties"].get("shapeName")
            a = agg.get(name)
            if a:
                lvl = next((s for s in severity if a[s.lower()] > 0), None)
                ft["properties"].update({
                    "risk_level": lvl if lvl else "DATA_UNAVAILABLE",
                    "risk_counts": {k: a[k] for k in
                                    ("critical", "high", "moderate", "low", "data_unavailable")},
                    "districts_total": a["total"],
                    "max_probability": a["max_probability"],
                    "worst_district": a["worst_district"],
                })
            else:
                ft["properties"].update({
                    "risk_level": "DATA_UNAVAILABLE", "risk_counts": None,
                    "districts_total": 0, "max_probability": None,
                    "worst_district": None,
                })
        return gj

    path = BASE_DIR / "data" / "processed" / "districts_simplified.geojson"
    if not path.exists():
        raise HTTPException(503, "District geometry not prepared yet.")
    gj = json.loads(_geo_text("districts_simplified.geojson"))

    order: list[str] = _district_shape_order()
    sid_to_did = {}
    for d in dmap.values():
        if d.shape_id in order:
            sid_to_did[d.shape_id] = d.id
    for ft in gj["features"]:
        sid = ft["properties"].get("shapeID")
        did = sid_to_did.get(sid)
        ra = by_id.get(did)
        ft["properties"]["district_id"] = did
        ft["properties"]["risk_level"] = ra.risk_level if ra else "DATA_UNAVAILABLE"
        ft["properties"]["probability"] = ra.probability if ra else None
        ft["properties"]["trend"] = ra.trend if ra else None
        ft["properties"]["data_cut"] = ra.data_cut.isoformat() if ra and ra.data_cut else None
    return gj


@app.get("/api/latest-data/{district_id}")
def latest_data(district_id: int, db: Session = Depends(get_db)):
    d = db.get(District, district_id)
    if not d:
        raise HTTPException(404, "district not found")
    out = {}
    for var in ("rain_mm", "soil_moisture_0_7cm", "river_discharge"):
        rows = (
            db.query(Observation)
            .filter(Observation.district_id == district_id, Observation.variable == var)
            .order_by(Observation.observed_at.desc())
            .limit(24)
            .all()
        )
        out[var] = [
            {"observed_at": o.observed_at.isoformat(), "value": o.value, "unit": o.unit,
             "source": o.source, "quality": o.quality}
            for o in rows
        ]
    return {"district": DistrictOut(id=d.id, name=d.name, state=d.state, lat=d.lat, lon=d.lon),
            "variables": out}


@app.get("/api/historical/{district_id}")
def historical(district_id: int, days: int = 365, db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(Observation)
        .filter(Observation.district_id == district_id, Observation.observed_at >= since)
        .order_by(Observation.observed_at)
        .all()
    )
    events = (
        db.query(FloodEvent)
        .filter(FloodEvent.matched_district_id == district_id,
                FloodEvent.from_date >= since)
        .order_by(FloodEvent.from_date)
        .all()
    )
    series: dict[str, list] = {}
    for o in rows:
        series.setdefault(o.variable, []).append(
            {"date": o.observed_at.date().isoformat(), "value": o.value})
    # deduplicate by day (last value wins)
    series = {k: list({x["date"]: x for x in v}.values()) for k, v in series.items()}
    return {
        "district": DistrictOut(id=district_id, name="", state="", lat=None, lon=None),
        "series": series,
        "flood_events": [
            {"event_id": e.event_id, "glide": e.glide, "name": e.name,
             "from_date": e.from_date.date().isoformat(),
             "to_date": e.to_date.date().isoformat() if e.to_date else None,
             "alert_level": e.alert_level, "source": e.source}
            for e in events
        ],
    }


_flood_history_cache: dict[int, tuple[date, dict]] = {}


@app.get("/api/flood-history/{district_id}")
def flood_history(district_id: int, db: Session = Depends(get_db)):
    """REAL hydrological history derived from the district's own observed
    discharge record. Every exceedance of the causal past-365d P99 GloFAS
    threshold is a MODELED HIGH-FLOW EPISODE — not automatically a confirmed
    flood. Officially reported events (GDACS) are returned separately so the
    UI can display the two side by side. Yearly + monthly aggregates included.
    Cached per day: episodes only change when observations do."""
    today = datetime.utcnow().date()
    cached = _flood_history_cache.get(district_id)
    if cached and cached[0] == today:
        return cached[1]

    rows = (
        db.query(Observation)
        .filter(Observation.district_id == district_id,
                Observation.variable == "river_discharge")
        .with_entities(Observation.observed_at, Observation.value)
        .order_by(Observation.observed_at)
        .all()
    )
    daily: dict = {}
    for ts, v in rows:
        daily[ts.date()] = float(v)  # last-of-day wins
    dates = sorted(daily)
    if len(dates) < 120:
        payload = {
            "district": DistrictOut(id=district_id, name="", state="", lat=None, lon=None),
            "available_from": dates[0].isoformat() if dates else None,
            "available_to": dates[-1].isoformat() if dates else None,
            "threshold_note": "Insufficient discharge history ingested for this district yet.",
            "threshold_value": None,
            "flood_days": [],
            "episodes": [],
            "yearly": [],
            "monthly": [],
            "official_events": [],
        }
        _flood_history_cache[district_id] = (today, payload)
        return payload

    vals = pd.Series([daily[x] for x in dates], index=dates, dtype=float)
    q99_series = vals.shift(1).rolling(365, min_periods=120).quantile(0.99)
    thr = float(vals.quantile(0.99))  # fixed whole-record threshold for the history view
    is_ex = (vals >= q99_series) & q99_series.notna()
    flood_days = [
        {"date": d.isoformat(), "value": float(vals.loc[d])}
        for d in dates if bool(is_ex.loc[d])
    ]

    # group consecutive exceedance days into episodes
    from .services.risk_engine import match_gdacs_name

    d_row = db.get(District, district_id)
    episodes: list[dict] = []
    for fd in flood_days:
        d0 = pd.Timestamp(fd["date"]).date()
        if episodes and (d0 - episodes[-1]["end"]).days <= 2:
            episodes[-1]["end"] = max(episodes[-1]["end"], d0)
            episodes[-1]["peak"] = max(episodes[-1]["peak"], fd["value"])
        else:
            episodes.append({"start": d0, "end": d0, "peak": fd["value"]})

    # real observed conditions during each episode window (rain / soil / discharge)
    def _window_conds(start: date, end: date) -> dict:
        lo = datetime.combine(start - timedelta(days=2), datetime.min.time())
        hi = datetime.combine(end + timedelta(days=2), datetime.min.time())
        obs = (db.query(Observation)
               .filter(Observation.district_id == district_id,
                       Observation.observed_at >= lo, Observation.observed_at <= hi,
                       Observation.variable.in_(
                           ["rain_mm", "soil_moisture_0_7cm", "river_discharge"]))
               .with_entities(Observation.variable, Observation.observed_at, Observation.value)
               .all())
        per: dict[str, dict] = {}
        for var, ts, v in obs:
            per.setdefault(var, {})[ts.date()] = float(v)
        max_rain = max(per["rain_mm"].values()) if per.get("rain_mm") else None
        mean_sm = (sum(per["soil_moisture_0_7cm"].values()) / len(per["soil_moisture_0_7cm"])) \
            if per.get("soil_moisture_0_7cm") else None
        max_dis = max(per["river_discharge"].values()) if per.get("river_discharge") else None
        return {
            "max_daily_rain_mm": round(max_rain, 1) if max_rain is not None else None,
            "mean_soil_moisture": round(mean_sm, 3) if mean_sm is not None else None,
            "peak_discharge_m3s": round(max_dis, 1) if max_dis is not None else None,
        }

    episodes_out = []
    for e in episodes:
        m = match_gdacs_name(db, d_row, e["start"], e["end"]) if d_row else None
        episodes_out.append({
            "start": e["start"].isoformat(), "end": e["end"].isoformat(),
            "peak_m3s": round(e["peak"], 1),
            "days": (e["end"] - e["start"]).days + 1,
            "conditions": _window_conds(e["start"], e["end"]),
            "officially_reported": m is not None,
            "event_name": m["name"] if m else None,
            "glide": m["glide"] if m else None,
            "alert_level": m["alert_level"] if m else None,
            "name_source": m["source"] if m else None,
            "official_event_name": m["name"] if m else None,
        })

    # official/reported events for this district (separate from modeled episodes)
    since = datetime.combine(dates[0], datetime.min.time())
    official = (
        db.query(FloodEvent)
        .filter(FloodEvent.matched_district_id == district_id,
                FloodEvent.from_date >= since)
        .order_by(FloodEvent.from_date)
        .all()
    )

    yearly: dict = {}
    monthly: dict = {}
    for d in dates:
        y = d.year
        m = f"{d.year}-{d.month:02d}"
        ex = bool(is_ex.loc[d])
        yearly.setdefault(y, {"year": y, "flood_days": 0, "peak": 0.0, "days": 0})
        yearly[y]["days"] += 1
        yearly[y]["peak"] = max(yearly[y]["peak"], daily[d])
        yearly[y]["flood_days"] += int(ex)
        monthly.setdefault(m, {"month": m, "flood_days": 0, "peak": 0.0})
        monthly[m]["peak"] = max(monthly[m]["peak"], daily[d])
        monthly[m]["flood_days"] += int(ex)

    payload = {
        "district": DistrictOut(id=district_id, name="", state="", lat=None, lon=None),
        "available_from": dates[0].isoformat(),
        "available_to": dates[-1].isoformat(),
        "threshold_value": round(thr, 1),
        "threshold_note": (
            "A day counts as a high-flow day when the river discharge exceeded this "
            "district's 99th-percentile discharge (computed from its own observed "
            "GloFAS record, lagged 1 day to stay causal). High-flow episodes are "
            "modeled hydrological events — not automatically officially reported floods."),
        "episode_term": "GloFAS-derived high-flow episode",
        "flood_days": flood_days,
        "episodes": episodes_out,
        "official_events": [
            {"event_id": e.event_id, "name": e.name, "glide": e.glide,
             "from_date": e.from_date.date().isoformat(),
             "to_date": e.to_date.date().isoformat() if e.to_date else None,
             "alert_level": e.alert_level, "source": e.source}
            for e in official
        ],
        "yearly": sorted(yearly.values(), key=lambda x: x["year"]),
        "monthly": sorted(monthly.values(), key=lambda x: x["month"]),
    }
    _flood_history_cache[district_id] = (today, payload)
    return payload


# ---------- browser notifications (internal; not a user subscription system) ----------
class _NotifyRegister(BaseModel):
    endpoint: str
    keys: dict
    district_id: int | None = None


@app.get("/api/notify/public-key")
def notify_public_key():
    """Application server key for the browser's notification setup."""
    from .services.webpush import public_key
    key = public_key()
    if not key:
        raise HTTPException(503, "Browser notifications are not configured on this server yet.")
    return {"publicKey": key}


@app.post("/api/notify/register")
def notify_register(body: _NotifyRegister, db: Session = Depends(get_db)):
    """Store the browser's internal push endpoint (anonymous; no account)."""
    from .services.webpush import register_endpoint, vapid_configured
    if not vapid_configured():
        raise HTTPException(503, "Browser notifications are not configured on this server yet.")
    try:
        register_endpoint(db, body.endpoint, body.keys.get("p256dh", ""),
                          body.keys.get("auth", ""), body.district_id)
        return {"ok": True}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/notify/unregister")
def notify_unregister(body: dict, db: Session = Depends(get_db)):
    from .services.webpush import deactivate_endpoint
    deactivate_endpoint(db, body.get("endpoint", ""))
    return {"ok": True}


@app.get("/api/notify/status")
def notify_status(db: Session = Depends(get_db)):
    """Server-side readiness only (no user data is exposed)."""
    from .services.webpush import count_active, vapid_configured
    return {"configured": vapid_configured(), "active_browsers": count_active(db)}


@app.post("/api/notify/test")
async def notify_test(db: Session = Depends(get_db)):
    """Controlled test push: latest HIGH-risk alert if one exists, else an
    honest test message. Uses the same delivery path as real alerts."""
    from .services.webpush import send_alert_to_all, vapid_configured
    if not vapid_configured():
        raise HTTPException(503, "Browser notifications are not configured on this server yet.")
    latest_high = (
        db.query(Alert)
        .filter(Alert.risk_level.in_(["HIGH", "CRITICAL"]))
        .order_by(Alert.created_at.desc())
        .first()
    )
    if latest_high:
        sent = send_alert_to_all(db, latest_high)
        return {"ok": True, "sent": sent, "used": f"latest {latest_high.risk_level} alert"}

    # no real HIGH alert right now: send an honest test payload
    import json as _json
    from .services.webpush import _load_push, _private_key, os_subject  # reuse sender
    payload = _json.dumps({
        "title": "🔔 Flood alert test",
        "body": ("Notifications are working. No district is currently at HIGH risk — "
                 "this is only a delivery test."),
        "url": "/alerts",
    })
    send = _load_push()["send"]
    from .models import BrowserNotifyEndpoint
    eps = db.query(BrowserNotifyEndpoint).filter(BrowserNotifyEndpoint.active.is_(True)).all()
    sent = 0
    for ep in eps:
        try:
            send(subscription_info={"endpoint": ep.endpoint,
                                    "keys": {"p256dh": ep.p256dh, "auth": ep.auth}},
                 data=payload, vapid_private_key=_private_key(),
                 vapid_claims={"sub": os_subject()})
            ep.last_success_at = datetime.utcnow()
            sent += 1
        except Exception as e:  # noqa: BLE001
            ep.last_error_at = datetime.utcnow()
            ep.last_error = str(e)[:200]
            if "410" in str(e) or "404" in str(e):
                ep.active = False
    db.commit()
    return {"ok": True, "sent": sent, "used": "honest test message (no HIGH alert exists)"}


# ---------- alerts ----------
@app.get("/api/alerts")
def alerts(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.query(Alert).order_by(Alert.created_at.desc()).limit(limit).all()
    return [
        AlertOut(
            id=a.id,
            district=db.get(District, a.district_id).name,
            state=db.get(District, a.district_id).state,
            alert_type=a.alert_type, risk_level=a.risk_level,
            probability=a.probability, message=a.message,
            created_at=a.created_at.isoformat(),
        ) for a in rows
    ]


@app.post("/api/subscriptions")
def subscribe(body: SubscribeIn, db: Session = Depends(get_db)):
    if body.district_id is None and (body.lat is None or body.lon is None):
        raise HTTPException(400, "provide district_id or lat/lon")
    district_id = body.district_id
    if district_id is None:
        districts = db.query(District).all()
        best, bd = None, 1e9
        for d in districts:
            dist = (d.lat - body.lat) ** 2 + (d.lon - body.lon) ** 2
            if dist < bd:
                best, bd = d.id, dist
        district_id = best
    if not db.get(District, district_id):
        raise HTTPException(404, "district not found")
    s = Subscription(email=body.email, district_id=district_id,
                     min_alert_type=body.min_alert_type or "ADVISORY",
                     lat=body.lat, lon=body.lon)
    db.add(s)
    db.commit()
    return SubscriptionOut(id=s.id, district_id=s.district_id,
                           email=s.email, min_alert_type=s.min_alert_type,
                           active=s.active)


@app.get("/api/subscriptions/{sub_id}")
def get_sub(sub_id: int, db: Session = Depends(get_db)):
    s = db.get(Subscription, sub_id)
    if not s:
        raise HTTPException(404, "not found")
    return SubscriptionOut(id=s.id, district_id=s.district_id, email=s.email,
                           min_alert_type=s.min_alert_type, active=s.active)


# ---------- transparency ----------
@app.get("/api/data-sources", response_model=list[SourceStatusOut])
def data_sources(db: Session = Depends(get_db)):
    rows = db.query(DataSourceStatus).all()
    if not rows:
        from .services.source_status import check_all
        rows = check_all_sync(db)
    return [SourceStatusOut(
        key=r.key, name=r.name, provider=r.provider, url=r.url,
        requires_auth=r.requires_auth, auth_env_var=r.auth_env_var,
        status=r.status, detail=r.detail,
        last_checked_at=r.last_checked_at.isoformat() if r.last_checked_at else None,
        last_success_at=r.last_success_at.isoformat() if r.last_success_at else None,
        last_data_at=r.last_data_at.isoformat() if r.last_data_at else None,
    ) for r in rows]


def check_all_sync(db: Session):
    """Run the async source check from a sync endpoint. The endpoint executes
    in a threadpool thread with no event loop (asyncio.get_event_loop() raises
    on Python 3.12+ there), so give it a fresh one explicitly."""
    import asyncio
    from .services.source_status import check_all
    return asyncio.run(check_all(db))


@app.post("/api/data-sources/check")
async def data_sources_check(db: Session = Depends(get_db)):
    from .services.source_status import check_all
    rows = await check_all(db)
    return {"checked": len(rows),
            "statuses": {r.key: r.status for r in rows}}


@app.post("/api/data-gov-in/ingest")
async def datagov_ingest(db: Session = Depends(get_db)):
    """Ingest REAL rows from configured data.gov.in resources (needs API key +
    resource IDs in env). Returns an honest report; never fabricates data."""
    from .data_sources.datagov import ingest_resources

    result = await ingest_resources(db)
    if result["status"] == "unauthorized":
        raise HTTPException(401, result["detail"])
    return result


@app.get("/api/model-info", response_model=ModelInfoOut)
def model_info(db: Session = Depends(get_db)):
    m = db.query(ModelInfo).filter(ModelInfo.is_active.is_(True)).first()
    if not m:
        raise HTTPException(503, "Model unavailable: no trained model registered. "
                                 "Run the ml training pipeline (see README).")
    card_path = BASE_DIR / "data" / "processed" / "model_card.json"
    fi_path = BASE_DIR / "data" / "processed" / "feature_importance.json"
    cal_path = BASE_DIR / "data" / "processed" / "calibration.json"
    card = json.loads(card_path.read_text()) if card_path.exists() else {}
    fi = json.loads(fi_path.read_text()) if fi_path.exists() else []
    cal = json.loads(cal_path.read_text()) if cal_path.exists() else {}
    ablation_path = BASE_DIR / "data" / "processed" / "ablation_report.json"
    ablation = json.loads(ablation_path.read_text()) if ablation_path.exists() else None
    return ModelInfoOut(
        model_version=m.model_version, algorithm=m.algorithm,
        trained_at=m.trained_at.isoformat(),
        training_period=f"{m.training_period_start.isoformat()}..{m.training_period_end.isoformat()}",
        validation_period=m.validation_period, test_period=m.test_period,
        features=json.loads(m.feature_list),
        metrics=json.loads(m.metrics),
        thresholds=json.loads(m.thresholds),
        target=card.get("selected_target"),
        target_definitions=card.get("target_definitions"),
        validation_selection=card.get("validation_selection"),
        feature_importance=fi,
        calibration=cal,
        probability_meaning=PROBABILITY_MEANING,
        prediction_horizon="0–24 h (nowcast of today's high-flow state)",
        ablation_study=ablation,
    )
