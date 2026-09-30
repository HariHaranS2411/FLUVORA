"""Download the verified geoBoundaries India ADM2 (districts) file.
URL + hash provenance recorded in DATA_SOURCE_MATRIX.md."""
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parents[1]
RAW = BASE / "data" / "raw"
URL = ("https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/"
       "gbOpen/IND/ADM2/geoBoundaries-IND-ADM2_simplified.geojson")


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    out = RAW / "geoBoundaries-IND-ADM2_simplified.geojson"
    if out.exists():
        print("already downloaded:", out)
        return
    resp = httpx.get(URL, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    out.write_bytes(resp.content)
    print(f"downloaded {len(resp.content)} bytes -> {out}")


if __name__ == "__main__":
    main()
