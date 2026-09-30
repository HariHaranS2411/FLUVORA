"""Fetch REAL historical flood events (GDACS / EC JRC-UN) and match each to a
district polygon via point-in-polygon.

The GDACS archive is not event-complete for India at district granularity —
this is documented as a label limitation (DATA_SOURCE_MATRIX.md §4). We use the
real events as-is and label district-days where a reported event was active.
Border-district matching allows cross-border coordinate offsets of ±0.75° for
events anchored slightly outside India.

Output: data/processed/flood_events.csv + flood_event_district_days.csv
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pandas as pd
from shapely.geometry import shape, Point

BASE = Path(__file__).resolve().parents[2]
PROCESSED = BASE / "data" / "processed"
RAW = BASE / "data" / "raw"

GDACS_URL = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"


async def fetch_events(start_year: int = 2015, end_year: int = 2026) -> pd.DataFrame:
    """Fetch real India-relevant events from GDACS: floods (FL) and cyclones (TC).
    Cyclones are included because many Indian floods are named cyclone events
    (e.g. MICHAUNG-23 behind Chennai's Dec-2023 flood)."""
    events: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=60.0) as client:
        for etype in ("FL", "TC"):
            for year in range(start_year, end_year + 1):
                params = {"eventtype": etype, "fromDate": f"{year}-01-01", "toDate": f"{year}-12-31"}
                try:
                    resp = await client.get(GDACS_URL, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                except (httpx.HTTPError, ValueError):
                    print(f"  gdacs {etype} {year}: failed")
                    continue
                feats = data.get("features", []) if isinstance(data, dict) else []
                kept = 0
                for ft in feats:
                    p = ft.get("properties", {})
                    # keep India events; TC tracks may carry empty iso3 while touching India
                    if etype == "FL" and str(p.get("iso3", "")).upper() != "IND":
                        continue
                    if etype == "TC" and str(p.get("iso3", "")).upper() not in ("IND", ""):
                        continue
                    coords = (ft.get("geometry") or {}).get("coordinates") or [None, None]
                    fd = str(p.get("fromdate", ""))[:10]
                    if not fd or coords[0] is None:
                        continue
                    ev = {
                        "event_id": f"{etype}-{p.get('eventid')}",
                        "glide": p.get("glide") or None,
                        "name": (p.get("eventname") or p.get("description") or
                                 ("Cyclone" if etype == "TC" else "Flood"))[:240],
                        "from_date": fd,
                        "to_date": str(p.get("todate", "")[:10]) or None,
                        "alert_level": p.get("alertlevel"),
                        "lat": float(coords[1]),
                        "lon": float(coords[0]),
                        "severity": (p.get("severitydata") or {}).get("severity"),
                    }
                    events[ev["event_id"]] = ev
                    kept += 1
                print(f"  gdacs {etype} {year}: {kept} India events")
    return pd.DataFrame(list(events.values()))


def match_events_to_districts(events: pd.DataFrame) -> pd.DataFrame:
    """Point-in-polygon match to the real ADM2 polygons; ±0.75° cross-border
    tolerance so events anchored just outside India can still attach."""
    gj = json.loads((RAW / "geoBoundaries-IND-ADM2_simplified.geojson").read_text(encoding="utf-8"))
    shapes = [(ft["properties"]["shapeID"], shape(ft["geometry"])) for ft in gj["features"]]

    rows = []
    for _, ev in events.iterrows():
        pt = Point(ev["lon"], ev["lat"])
        hit = None
        for shape_id, geom in shapes:
            if geom.covers(pt):
                hit = shape_id
                break
        if hit is None:
            for dx in (-0.75, 0.75, 0.0):
                if hit:
                    break
                for dy in (-0.75, 0.75, 0.0):
                    if dx == 0 and dy == 0:
                        continue
                    p2 = Point(ev["lon"] + dx, ev["lat"] + dy)
                    for shape_id, geom in shapes:
                        if geom.covers(p2):
                            hit = shape_id
                            break
                    if hit:
                        break
        if hit:
            rows.append({"event_id": ev["event_id"], "shape_id": hit,
                         "date": ev["from_date"], "to_date": ev["to_date"]})
    return pd.DataFrame(rows, columns=["event_id", "shape_id", "date", "to_date"])


def expand_event_days(events: pd.DataFrame, matches: pd.DataFrame) -> pd.DataFrame:
    """Label district-DAYS: for each matched event, mark each day from from_date
    to min(to_date, from_date+7d) as a flood-report day for that district."""
    if matches.empty:
        return pd.DataFrame(columns=["shape_id", "date"])
    ev = events.set_index("event_id")
    rows = []
    for _, m in matches.iterrows():
        start = pd.Timestamp(m["date"])
        end = pd.Timestamp(m["to_date"]) if m["to_date"] else start
        end = min(end, start + pd.Timedelta(days=7))  # cap duration; guard against bad todates
        days = pd.date_range(start, end, freq="D")
        for d in days:
            rows.append({"shape_id": m["shape_id"], "date": d.date().isoformat(),
                         "event_id": m["event_id"]})
    return pd.DataFrame(rows)


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    events = asyncio.run(fetch_events(2015, 2026))
    events.to_csv(PROCESSED / "flood_events.csv", index=False)
    print(f"total India flood events: {len(events)}")

    matches = match_events_to_districts(events)
    matches.to_csv(PROCESSED / "flood_event_districts.csv", index=False)
    print(f"matched to districts: {len(matches)}")

    days = expand_event_days(events, matches)
    days.to_csv(PROCESSED / "flood_event_district_days.csv", index=False)
    print(f"district-day labels: {len(days)}")


if __name__ == "__main__":
    main()
