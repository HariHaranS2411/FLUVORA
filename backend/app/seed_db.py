"""Seed the database with REAL prepared data:
  - districts (from data/processed/districts.csv, real geoBoundaries geometry)
  - flood events + matches (from real GDACS archive)
  - model registry entry (from model_card.json produced by ml/training)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from shapely.geometry import shape

from .config import BASE_DIR
from .database import SessionLocal, engine
from .models import Base, District, FloodEvent, ModelInfo

PROCESSED = BASE_DIR / "data" / "processed"


def seed() -> None:
    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        districts = json.loads(
            (BASE_DIR / "data" / "processed" / "districts_simplified.geojson").read_text(encoding="utf-8")
        )
        import pandas as pd

        meta = pd.read_csv(PROCESSED / "districts.csv")

        n, updated = 0, 0
        for ft in districts["features"]:
            shape_id = ft["properties"]["shapeID"]
            row = meta[meta["shape_id"] == shape_id]
            if row.empty:
                continue
            row = row.iloc[0]
            geom = shape(ft["geometry"])
            existing = db.query(District).filter(District.shape_id == shape_id).first()
            if existing:
                existing.name = str(row["name"])
                existing.state = str(row["state"])
                existing.lat = float(row["lat"])
                existing.lon = float(row["lon"])
                existing.area_km2 = round(geom.area * 111.32 * 111.32, 1)
                updated += 1
            else:
                db.add(District(
                    name=str(row["name"]),
                    state=str(row["state"]),
                    shape_id=shape_id,
                    lat=float(row["lat"]),
                    lon=float(row["lon"]),
                    area_km2=round(geom.area * 111.32 * 111.32, 1),
                    geometry_json=json.dumps(ft["geometry"]),
                ))
                n += 1
        db.commit()
        print(f"districts added: {n}, updated: {updated}")

        events_csv = PROCESSED / "flood_events.csv"
        if events_csv.exists():
            import pandas as pd

            ev = pd.read_csv(events_csv)
            matches = pd.read_csv(PROCESSED / "flood_event_districts.csv")
            id2did = {d.shape_id: d.id for d in db.query(District).all()}
            for _, r in ev.iterrows():
                m = matches[matches["event_id"] == r["event_id"]]
                did = id2did.get(m.iloc[0]["shape_id"]) if not m.empty else None
                db.merge(FloodEvent(
                    source="gdacs",
                    event_id=str(r["event_id"]),
                    glide=r.get("glide"),
                    name=str(r["name"])[:240],
                    country_iso="IND",
                    from_date=datetime.fromisoformat(str(r["from_date"])),
                    to_date=datetime.fromisoformat(str(r["to_date"])) if r.get("to_date") else None,
                    alert_level=r.get("alert_level"),
                    lat=float(r["lat"]),
                    lon=float(r["lon"]),
                    severity=float(r["severity"]) if r.get("severity") == r.get("severity") else None,
                    matched_district_id=did,
                ))
            db.commit()
            print(f"flood events seeded: {len(ev)}")

        card_path = PROCESSED / "model_card.json"
        if card_path.exists():
            card = json.loads(card_path.read_text())
            # only the newest trained model stays active; upsert by version
            db.query(ModelInfo).update({ModelInfo.is_active: False})
            existing_m = db.query(ModelInfo).filter(
                ModelInfo.model_version == card["model_version"]).first()
            if existing_m is None:
                existing_m = ModelInfo(model_version=card["model_version"])
                db.add(existing_m)
            existing_m.algorithm = card["selected_algorithm"]
            existing_m.trained_at = datetime.now()
            existing_m.training_period_start = datetime.strptime(
                card["periods"]["train"][:10], "%Y-%m-%d").date()
            existing_m.training_period_end = datetime.strptime(
                card["periods"]["train"][-10:], "%Y-%m-%d").date()
            existing_m.validation_period = card["periods"]["validation"]
            existing_m.test_period = card["periods"]["test"]
            existing_m.feature_list = json.dumps(card["features"])
            existing_m.metrics = json.dumps(card["test_metrics"])
            existing_m.thresholds = json.dumps(card["thresholds"])
            existing_m.artifact_path = str(BASE_DIR / "ml" / "models" / "model.joblib")
            existing_m.is_active = True
            db.commit()
            print("model registry seeded:", card["model_version"])
    finally:
        db.close()


if __name__ == "__main__":
    seed()
