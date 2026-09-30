"""Pydantic schemas for API responses."""
from pydantic import BaseModel


class DistrictOut(BaseModel):
    id: int
    name: str
    state: str
    lat: float | None = None
    lon: float | None = None


class RiskMapItem(BaseModel):
    district_id: int
    name: str
    state: str
    lat: float
    lon: float
    probability: float | None
    risk_probability: float | None = None
    risk_level: str | None
    state_label: str | None = None
    prediction_horizon: str | None = None
    probability_meaning: str | None = None
    data_quality: str | None = None
    trend: str
    confidence: float | None
    data_cut: str | None
    last_updated: str | None = None
    missing_critical: bool


class Counts(BaseModel):
    critical: int
    high: int
    moderate: int
    low: int
    data_unavailable: int


class RiskMapOut(BaseModel):
    computed_at: str | None
    districts: list[RiskMapItem]
    counts: Counts | None


class ModelInfoOut(BaseModel):
    model_version: str
    algorithm: str
    trained_at: str
    training_period: str
    validation_period: str
    test_period: str
    features: list[str]
    metrics: dict
    thresholds: dict
    target: str | None = None
    target_definitions: dict | None = None
    validation_selection: dict | None = None
    feature_importance: list = []
    calibration: dict = {}
    probability_meaning: str | None = None
    prediction_horizon: str | None = None
    ablation_study: dict | None = None


class SourceStatusOut(BaseModel):
    key: str
    name: str
    provider: str
    url: str
    requires_auth: bool
    auth_env_var: str | None
    status: str
    detail: str
    last_checked_at: str | None
    last_success_at: str | None
    last_data_at: str | None


class SubscribeIn(BaseModel):
    email: str | None = None
    district_id: int | None = None
    lat: float | None = None
    lon: float | None = None
    min_alert_type: str | None = None


class SubscriptionOut(BaseModel):
    id: int
    district_id: int | None
    email: str | None
    min_alert_type: str
    active: bool


class AlertOut(BaseModel):
    id: int
    district: str
    state: str
    alert_type: str
    risk_level: str
    probability: float | None
    message: str
    created_at: str
