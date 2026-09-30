"""Prepare district geometry from verified GeoBoundaries India ADM2 data.

- Computes real polygon centroids (shapely) for each of the 735 districts.
- Assigns each district to a State/UT via point-in-polygon against ADM1
  (real geoBoundaries ADM1 tier), because ADM2 carries no state attribute.

Outputs:
  data/processed/districts.csv                 (name, state, lat, lon, shape_id)
  data/processed/districts_simplified.geojson  (map geometry + metadata)
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from shapely.geometry import shape, Point

BASE = Path(__file__).resolve().parents[2]          # flash-flood-ai/
RAW_ADM2 = BASE / "data" / "raw" / "geoBoundaries-IND-ADM2_simplified.geojson"
OUT_CSV = BASE / "data" / "processed" / "districts.csv"
OUT_GEOJSON = BASE / "data" / "processed" / "districts_simplified.geojson"
STATE_LOOKUP_CACHE = BASE / "data" / "processed" / "state_lookup.json"


def _ensure_states_file() -> Path:
    """Download the real ADM1 (State/UT) polygons once; cached on disk."""
    import httpx

    adm1_path = BASE / "data" / "raw" / "geoBoundaries-IND-ADM1_simplified.geojson"
    if adm1_path.exists():
        return adm1_path
    resp = httpx.get(
        "https://www.geoboundaries.org/api/current/gbOpen/IND/ADM1/",
        timeout=30, follow_redirects=True,
    )
    resp.raise_for_status()
    meta = resp.json()
    gj = httpx.get(meta["simplifiedGeometryGeoJSON"], timeout=60, follow_redirects=True)
    gj.raise_for_status()
    adm1_path.write_text(gj.text, encoding="utf-8")
    return adm1_path


def prepare_districts() -> pd.DataFrame:
    if not RAW_ADM2.exists():
        raise FileNotFoundError(
            "Run scripts/download_boundaries.py first — no fabricated geometry is allowed."
        )

    adm2 = json.loads(RAW_ADM2.read_text(encoding="utf-8"))
    adm1_path = _ensure_states_file()
    adm1 = json.loads(adm1_path.read_text(encoding="utf-8"))
    state_shapes = [
        (ft["properties"].get("shapeName", f"ADM1_{i}"), shape(ft["geometry"]))
        for i, ft in enumerate(adm1["features"])
    ]

    rows = []
    for ft in adm2["features"]:
        geom = shape(ft["geometry"])
        c = geom.centroid
        # State assignment must be robust: an island district's centroid can fall
        # in the sea (Nicobars, Lakshadweep) or between discontinuous parts
        # (Uttar Dinajpur). Try centroid -> representative point -> largest
        # ADM1 overlap; a district is only "Unknown" if all three fail.
        state = "Unknown"
        for pt in (c, geom.representative_point()):
            for state_name, poly in state_shapes:
                if poly.covers(pt):
                    state = state_name
                    break
            if state != "Unknown":
                break
        if state == "Unknown":
            best = 0.0
            for state_name, poly in state_shapes:
                a = poly.intersection(geom).area
                if a > best:
                    best, state = a, state_name
        rows.append({
            "name": ft["properties"]["shapeName"],
            "state": state,
            "lat": round(c.y, 4),
            "lon": round(c.x, 4),
            "shape_id": ft["properties"]["shapeID"],
        })

    df = pd.DataFrame(rows)
    # Real duplicate names across states (e.g. Aurangabad MH/BR, Bilaspur HP/CG):
    # disambiguate with the real state name, never silently drop districts.
    dup = df.duplicated(subset=["name"], keep=False)
    df.loc[dup, "name"] = df.loc[dup, "name"] + " (" + df.loc[dup, "state"] + ")"
    df = df.sort_values(["state", "name"]).reset_index(drop=True)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_CSV, index=False)

    # Map GeoJSON with metadata per feature
    feats_out = []
    for ft in adm2["features"]:
        props = ft["properties"]
        feats_out.append({
            "type": "Feature",
            "properties": {"shapeName": props["shapeName"], "shapeID": props["shapeID"]},
            "geometry": ft["geometry"],
        })
    OUT_GEOJSON.write_text(
        json.dumps({"type": "FeatureCollection", "features": feats_out}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"districts prepared: {len(df)} | states resolved: {(df['state'] != 'Unknown').sum()}")
    return df


if __name__ == "__main__":
    prepare_districts()
