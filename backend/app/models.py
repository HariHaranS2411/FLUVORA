"""SQLAlchemy models. Every stored observation keeps its source, timestamps and
quality flag so the UI can always show provenance (spec §7, §19, §28)."""
from datetime import datetime, date

from sqlalchemy import (
    String, Integer, Float, DateTime, Date, Boolean, Text, ForeignKey, Index, UniqueConstraint
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class District(Base):
    __tablename__ = "districts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    state: Mapped[str] = mapped_column(String(120), index=True)
    shape_id: Mapped[str] = mapped_column(String(64), unique=True)   # geoBoundaries shapeID
    lat: Mapped[float] = mapped_column(Float)                        # polygon centroid
    lon: Mapped[float] = mapped_column(Float)
    elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    slope_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    area_km2: Mapped[float | None] = mapped_column(Float, nullable=True)
    geometry_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    observations: Mapped[list["Observation"]] = relationship(back_populates="district")


class Observation(Base):
    """One measurement of one variable for one district on one timestamp.

    Stores raw values exactly as received from the source; derived features are
    computed downstream (ml/feature_engineering), never fabricated here.
    """
    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    district_id: Mapped[int] = mapped_column(ForeignKey("districts.id"), index=True)
    source: Mapped[str] = mapped_column(String(64))          # e.g. "open-meteo-era5"
    variable: Mapped[str] = mapped_column(String(64))        # e.g. "rain_24h_mm"
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(24))
    quality: Mapped[str] = mapped_column(String(16), default="ok")  # ok|missing|stale

    district: Mapped["District"] = relationship(back_populates="observations")

    __table_args__ = (
        UniqueConstraint("district_id", "source", "variable", "observed_at",
                         name="uq_observation"),
        Index("ix_obs_var_time", "variable", "observed_at"),
        Index("ix_obs_district_var_time", "district_id", "variable", "observed_at"),
    )


class FloodEvent(Base):
    """Historical flood/cyclone reports (GDACS / JRC-UN archive). Real events only."""
    __tablename__ = "flood_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), default="gdacs")
    event_id: Mapped[str] = mapped_column(String(32), index=True)    # GDACS event id
    glide: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(240))
    country_iso: Mapped[str] = mapped_column(String(4))
    from_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    to_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    alert_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    severity: Mapped[float | None] = mapped_column(Float, nullable=True)
    matched_district_id: Mapped[int | None] = mapped_column(
        ForeignKey("districts.id"), nullable=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("source", "event_id", name="uq_flood_event"),
    )


class ModelInfo(Base):
    """Model registry (spec §23). Every prediction references model_version."""
    __tablename__ = "model_registry"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version: Mapped[str] = mapped_column(String(32), unique=True)
    algorithm: Mapped[str] = mapped_column(String(64))
    trained_at: Mapped[datetime] = mapped_column(DateTime)
    training_period_start: Mapped[date] = mapped_column(Date)
    training_period_end: Mapped[date] = mapped_column(Date)
    validation_period: Mapped[str] = mapped_column(String(64))
    test_period: Mapped[str] = mapped_column(String(64))
    feature_list: Mapped[str] = mapped_column(Text)          # JSON list
    metrics: Mapped[str] = mapped_column(Text)               # JSON metrics
    thresholds: Mapped[str] = mapped_column(Text)            # JSON calibrated thresholds
    artifact_path: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)


class RiskAssessment(Base):
    """Current risk output per district per run — fully traceable."""
    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    district_id: Mapped[int] = mapped_column(ForeignKey("districts.id"), index=True)
    model_version: Mapped[str] = mapped_column(String(32))
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    trend: Mapped[str] = mapped_column(String(24), default="stable")
    history_similarity: Mapped[str | None] = mapped_column(String(32), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    drivers: Mapped[str | None] = mapped_column(Text, nullable=True)      # JSON list
    input_freshness: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON map
    missing_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    data_cut: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # newest input ts

    __table_args__ = (
        UniqueConstraint("district_id", "computed_at", name="uq_risk_run"),
        Index("ix_risk_assessments_computed_all", "computed_at", "district_id"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    district_id: Mapped[int] = mapped_column(ForeignKey("districts.id"), index=True)
    alert_type: Mapped[str] = mapped_column(String(24))   # WATCH|ADVISORY|HIGH_RISK|IMMINENT|DATA_UNAVAILABLE
    risk_level: Mapped[str] = mapped_column(String(16))
    probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    event_key: Mapped[str] = mapped_column(String(80))    # dedup key
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("district_id", "event_key", name="uq_alert_dedup"),
    )


class Subscription(Base):
    """Location-based alert subscriptions. Stores minimum location info only (spec §14)."""
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    district_id: Mapped[int | None] = mapped_column(ForeignKey("districts.id"), nullable=True)
    min_alert_type: Mapped[str] = mapped_column(String(24), default="ADVISORY")
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BrowserNotifyEndpoint(Base):
    """Internal browser-notification endpoint registry (Web Push).

    NOT a user subscription system: no account, email, or personal data —
    only the browser-generated push endpoint URL and its keys, plus which
    district the browser last viewed (optional, for targeted alerts).
    User-facing UI never mentions this table; the only control is
    "Enable Notifications".
    """
    __tablename__ = "browser_notify_endpoints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(512), unique=True)  # browser push URL
    p256dh: Mapped[str] = mapped_column(String(128))                 # browser public key
    auth: Mapped[str] = mapped_column(String(64))                    # browser auth secret
    last_district_id: Mapped[int | None] = mapped_column(
        ForeignKey("districts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(200), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DataSourceStatus(Base):
    """Live connection-check results shown on the Data Sources page (spec §19)."""
    __tablename__ = "data_source_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(48), unique=True)   # e.g. open-meteo-era5
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(120))
    url: Mapped[str] = mapped_column(String(240))
    requires_auth: Mapped[bool] = mapped_column(Boolean, default=False)
    auth_env_var: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="unknown")  # connected|unreachable|unauthorized|no_public_api
    detail: Mapped[str] = mapped_column(String(400), default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_data_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # newest obs
