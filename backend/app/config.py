"""Central configuration. Only environment variables for services that genuinely
need credentials live here (see DATA_SOURCE_MATRIX.md — the primary sources used
by this system require NO keys)."""
from pathlib import Path

from pydantic_settings import BaseSettings

BASE_DIR = Path(__file__).resolve().parents[2]  # flash-flood-ai/


class Settings(BaseSettings):
    app_name: str = "FLUVORA — India Flash Flood Early Warning"

    # Database: SQLite locally; set DATABASE_URL to a PostGIS URL in production.
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"

    # Comma-separated CORS origins. Localhost Vite dev defaults; set CORS_ORIGINS
    # in production to the deployed frontend URL(s), e.g. on Render.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # --- Optional credentials (sources that require registration) ---
    nasa_power_api_key: str = ""        # https://power.larc.nasa.gov/ (free, higher limits)
    reliefweb_appname: str = ""         # https://apidoc.reliefweb.int (request appname)
    data_gov_in_api_key: str = ""       # https://data.gov.in (per-dataset keys)

    # Feature-service base URLs (overridable for self-hosted instances)
    open_meteo_archive_url: str = "https://archive-api.open-meteo.com/v1/archive"
    open_meteo_forecast_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_flood_url: str = "https://flood-api.open-meteo.com/v1/flood"
    open_meteo_elevation_url: str = "https://api.open-meteo.com/v1/elevation"
    gdacs_api_url: str = "https://www.gdacs.org/gdacsapi/api/events/geteventlist/SEARCH"
    geoboundaries_api_url: str = "https://www.geoboundaries.org/api/current/gbOpen/IND/ADM2/"

    # Fetch tuning
    http_timeout: float = 30.0
    fetch_batch_size: int = 20          # districts per Open-Meteo batch call
    fetch_pause_seconds: float = 1.0

    # Ingestion window
    history_years: int = 10             # ERA5 history for training (2015-09 .. 2025-09)
    history_start: str = "2015-09-01"

    # Refresh schedule (seconds)
    refresh_interval_seconds: int = 1800  # 30 min

    # Web Push (browser notifications for HIGH-risk alerts). Keys are generated
    # once (see app/services/webpush.py:generate_vapid_keys) and kept server-side.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = ""

    model_config = {"env_file": str(BASE_DIR / ".env"), "env_prefix": "", "extra": "ignore"}


settings = Settings()
