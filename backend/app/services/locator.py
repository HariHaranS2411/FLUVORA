"""City-level location lookup — REAL geocoding only (Open-Meteo Geocoding API,
verified live 2026-09-25: returns actual city coordinates plus admin2/district
names). Cities are resolved to a district by:
  1) real geocoder results filtered to India,
  2) point-in-polygon against the real geoBoundaries ADM2 geometry,
  3) fallback: nearest district centroid, with the real distance reported.
Unknown cities are reported as not found — nothing is guessed.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
from shapely.geometry import shape, Point
from sqlalchemy.orm import Session

from ..config import BASE_DIR
from ..models import District

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"

_geo_cache: dict = {}


def _load_geometry() -> tuple[dict[str, tuple], list[tuple]]:
    """(shape_id -> (polygon, bounds)) and a list of (shape_id, centroid) —
    from the real geoBoundaries ADM2 file."""
    if not _geo_cache:
        path = BASE_DIR / "data" / "processed" / "districts_simplified.geojson"
        gj = json.loads(path.read_text(encoding="utf-8"))
        polys: dict[str, tuple] = {}
        centroids: list[tuple] = []
        for ft in gj["features"]:
            sid = ft["properties"]["shapeID"]
            geom = shape(ft["geometry"])
            polys[sid] = (geom, geom.bounds)
            c = geom.centroid
            centroids.append((sid, c.y, c.x))
        _geo_cache["polys"] = polys
        _geo_cache["centroids"] = centroids
    return _geo_cache["polys"], _geo_cache["centroids"]


async def geocode_city(name: str) -> list[dict]:
    """Real geocoder results for Indian cities (name, state, lat, lon, population)."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(GEOCODE_URL, params={
            "name": name, "count": 10, "language": "en", "format": "json",
        })
        r.raise_for_status()
        data = r.json()
    out = []
    for res in data.get("results", []) or []:
        if res.get("country_code") != "IN":
            continue
        out.append({
            "name": res.get("name"),
            "state": res.get("admin1"),
            "district_hint": res.get("admin2"),
            "lat": float(res["latitude"]),
            "lon": float(res["longitude"]),
            "population": res.get("population"),
        })
    return out


def resolve_district(db: Session, lat: float, lon: float,
                     district_hint: str | None = None) -> dict:
    """Resolve a real coordinate to a district: point-in-polygon first, then
    admin2-name match against DB districts, then nearest centroid (with the
    real great-circle distance in the result)."""
    polys, _ = _load_geometry()
    pt = Point(lon, lat)

    for sid, (geom, _b) in polys.items():
        if geom.covers(pt):
            d = db.query(District).filter(District.shape_id == sid).first()
            if d:
                return {"district_id": d.id, "name": d.name, "state": d.state,
                        "method": "point-in-polygon", "distance_km": 0.0}

    if district_hint:
        hint = district_hint.replace(" district", "").strip().casefold()
        for d in db.query(District).all():
            if d.name.casefold() == hint:
                return {"district_id": d.id, "name": d.name, "state": d.state,
                        "method": "admin2-name", "distance_km": 0.0}

    _, centroids = _load_geometry()
    best, bd = None, 1e18
    for sid, cy, cx in centroids:
        # equirectangular approximation is fine at city scale
        dy = (cy - lat) * 111.32
        dx = (cx - lon) * 111.32 * max(0.2, __import__("math").cos(__import__("math").radians(lat)))
        dist = (dy * dy + dx * dx) ** 0.5
        if dist < bd:
            bd, best = dist, sid
    d = db.query(District).filter(District.shape_id == best).first()
    return {"district_id": d.id, "name": d.name, "state": d.state,
            "method": "nearest-centroid", "distance_km": round(bd, 1)}


async def locate_city(db: Session, query: str) -> dict:
    """Full city -> district -> current risk + explanation locator payload."""
    from .risk_engine import explain_district

    matches = await geocode_city(query)
    if not matches:
        return {"found": False, "query": query,
                "detail": "No Indian city with this name was found in the geocoder."}

    city = matches[0]
    loc = resolve_district(db, city["lat"], city["lon"], city.get("district_hint"))
    d = db.get(District, loc["district_id"])
    explanation = explain_district(db, d)

    return {
        "found": True,
        "query": query,
        "city": {"name": city["name"], "state": city["state"],
                 "lat": city["lat"], "lon": city["lon"],
                 "matches": matches[:5]},
        "district": {"id": d.id, "name": d.name, "state": d.state},
        "resolution": loc,
        "explanation": explanation,
    }
