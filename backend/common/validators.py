"""Pydantic request-body models: the validation layer used instead of DRF serializers."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError, field_validator, model_validator

from common.exceptions import ValidationError as ApiValidationError

RoleName = Literal["administrator", "climate_analyst", "viewer", "admin", "analyst", "stakeholder"]
Variable = Literal["temperature", "precipitation", "humidity", "wind_speed", "pressure",
                   "cloud_cover", "solar_radiation", "temperature_max", "temperature_min"]
Granularity = Literal["instantaneous", "hourly", "daily", "monthly"]


def parse(model: type[BaseModel], payload: Any) -> BaseModel:
    try:
        return model.model_validate(payload or {})
    except ValidationError as exc:
        details = [f"{'.'.join(str(p) for p in err['loc']) or 'body'}: {err['msg']}" for err in exc.errors()]
        raise ApiValidationError("Request body failed validation.", details=details) from exc


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginRequest(Strict):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)
    device_hint: Optional[str] = Field(default=None, max_length=120)


class RegisterRequest(Strict):
    full_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    organisation: Optional[str] = Field(default=None, max_length=140)

    @field_validator("email")
    @classmethod
    def _email_shape(cls, value: str) -> str:
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("enter a valid email address")
        return value


class RefreshRequest(Strict):
    refresh_token: str = Field(min_length=16, max_length=400)


class LogoutRequest(Strict):
    refresh_token: Optional[str] = Field(default=None, max_length=400)
    all_sessions: bool = False


class PasswordChangeRequest(Strict):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)


class PasswordResetRequestRequest(Strict):
    email: str = Field(min_length=5, max_length=254)
    redirect_origin: Optional[str] = Field(default=None, max_length=200)


class PasswordResetConfirm(Strict):
    token: str = Field(min_length=16, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)


class ProfileUpdate(Strict):
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    preferences: Optional[dict[str, Any]] = None


class UserCreate(Strict):
    full_name: str = Field(min_length=2, max_length=120)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    role: RoleName = "viewer"
    must_change_password: bool = True
    email_verified: bool = False


class UserPatch(Strict):
    role: Optional[RoleName] = None
    is_active: Optional[bool] = None
    full_name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    reset_password: Optional[str] = Field(default=None, min_length=8, max_length=128)


class DatasetCreate(Strict):
    name: str = Field(min_length=2, max_length=160)
    description: Optional[str] = Field(default=None, max_length=2000)
    source_type: Literal["api", "file", "stream", "sample"] = "file"
    source_name: str = Field(min_length=2, max_length=120)
    source_url: Optional[str] = Field(default=None, max_length=500)
    format: Literal["csv", "excel", "json", "jsonl", "netcdf", "parquet", "api"] = "csv"
    geographic_coverage: Optional[dict[str, Any]] = None
    temporal_coverage: Optional[dict[str, Any]] = None
    tags: list[str] = Field(default_factory=list, max_length=20)


class IngestionJobRequest(Strict):
    provider: Literal["nasa_power", "noaa_cdc", "open_meteo"]
    dataset_name: str = Field(min_length=2, max_length=160)
    coordinates: dict[str, float] = Field(description="{'latitude': .., 'longitude': ..}")
    date_range: Optional[dict[str, str]] = None
    variables: list[str] = Field(default_factory=list, max_length=12)
    granularity: Granularity = "daily"
    station_id: Optional[str] = Field(default=None, max_length=64)
    dataset_code: Optional[str] = Field(default=None, max_length=64)
    location_code: Optional[str] = Field(default=None, max_length=64)
    location_name: Optional[str] = Field(default=None, max_length=80)

    @field_validator("coordinates")
    @classmethod
    def _coords(cls, value: dict[str, float]) -> dict[str, float]:
        lat = float(value.get("latitude", 91))
        lon = float(value.get("longitude", 181))
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            raise ValueError("latitude must be -90..90 and longitude -180..180")
        return {"latitude": lat, "longitude": lon}

    @model_validator(mode="after")
    def _window(self):
        if self.date_range:
            start = self.date_range.get("start")
            end = self.date_range.get("end")
            if not start or not end:
                raise ValueError("date_range requires 'start' and 'end' (YYYY-MM-DD)")
            try:
                s = datetime.strptime(start, "%Y-%m-%d")
                e = datetime.strptime(end, "%Y-%m-%d")
            except ValueError as exc:
                raise ValueError("dates must use YYYY-MM-DD (NASA POWER) or ISO format") from exc
            if s > e:
                raise ValueError("date_range.start must be before date_range.end")
            if (e - s).days > 3 * 366:
                raise ValueError("date range exceeds the 3-year limit per ingestion job")
        return self


class UploadValidateRequest(Strict):
    file_ref: str = Field(min_length=4, max_length=120)
    column_map: dict[str, str] = Field(default_factory=dict)
    units_hint: dict[str, str] = Field(default_factory=dict)
    timezone: Optional[str] = Field(default=None, max_length=60)
    granularity: Granularity = "daily"
    dry_run_limit: int = Field(default=5000, ge=1, le=200_000)


class UploadCommitRequest(UploadValidateRequest):
    dataset_id: Optional[str] = Field(default=None, max_length=40)
    dataset: Optional[DatasetCreate] = None
    overwrite_duplicates: Literal["skip", "replace", "fail"] = "skip"


class ProcessingJobRequest(Strict):
    workflow: str = Field(min_length=3, max_length=80)
    dataset_id: Optional[str] = Field(default=None, max_length=40)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @field_validator("parameters")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 12:
            raise ValueError("too many parameters; workflows accept at most 12")
        for key, item in value.items():
            if isinstance(item, str) and len(item) > 300:
                raise ValueError(f"parameter '{key}' is too long")
            if isinstance(item, (list, dict)) and len(str(item)) > 2000:
                raise ValueError(f"parameter '{key}' is too large")
        return value


class TrainingRequest(Strict):
    target_variable: Variable
    granularity: Granularity = "daily"
    horizon_steps: int = Field(ge=1, le=365)
    region: Optional[str] = Field(default=None, max_length=80)
    station_id: Optional[str] = Field(default=None, max_length=64)
    date_range: Optional[dict[str, str]] = None
    models: list[str] = Field(default_factory=lambda: ["baseline", "linear", "random_forest"], max_length=6)
    register_as_candidate: bool = True
    auto_promote: bool = False


class ForecastRequest(Strict):
    model_ref: str = Field(min_length=3, max_length=120)
    target_variable: Variable
    region: Optional[str] = Field(default=None, max_length=80)
    station_id: Optional[str] = Field(default=None, max_length=64)
    horizon_steps: int = Field(ge=1, le=365)
    origin_date: Optional[str] = Field(default=None, max_length=40)


class AnomalyScanRequest(Strict):
    variable: Variable = "temperature"
    method: Literal["threshold", "statistical", "isolation_forest"] = "statistical"
    region: Optional[str] = Field(default=None, max_length=80)
    window_days: int = Field(default=90, ge=7, le=1500)
    sigma: float = Field(default=3.0, ge=1.5, le=8.0)


class AlertRuleRequest(Strict):
    name: str = Field(min_length=2, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    enabled: bool = True
    target_variable: Variable
    condition: Literal["above", "below", "anomaly", "trend_change", "forecast_above", "forecast_below"]
    threshold: Optional[float] = None
    geographic_scope: dict[str, Any] = Field(default_factory=dict)
    data_source: Optional[str] = Field(default=None, max_length=60)
    severity: Literal["info", "warning", "severe"] = "warning"
    cooldown_minutes: int = Field(default=180, ge=5, le=10080)
    recipients: list[str] = Field(default_factory=list, max_length=25)
    notify_channels: list[Literal["in_app", "email"]] = Field(default_factory=lambda: ["in_app"])

    @model_validator(mode="after")
    def _needs_threshold(self):
        if self.condition in ("above", "below", "forecast_above", "forecast_below") and self.threshold is None:
            raise ValueError("threshold is required for comparison conditions")
        return self


class ReportRequest(Strict):
    report_type: Literal["climate_trends", "regional_comparison", "dataset_quality",
                         "anomalies", "forecast_results", "processing_performance",
                         "ingestion_history"]
    title: Optional[str] = Field(default=None, max_length=160)
    filters: dict[str, Any] = Field(default_factory=dict)
    formats: list[Literal["csv", "pdf", "json"]] = Field(default_factory=lambda: ["csv"])

    @field_validator("filters")
    @classmethod
    def _bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 16:
            raise ValueError("too many filters")
        return value


class TicketRequest(Strict):
    subject: str = Field(min_length=4, max_length=160)
    body: str = Field(min_length=10, max_length=6000)
    category: Literal["technical", "data_quality", "access", "feature_request", "billing", "other"] = "other"
    priority: Literal["low", "medium", "high"] = "medium"


class TicketMessageRequest(Strict):
    body: str = Field(min_length=2, max_length=6000)


class TicketPatchRequest(Strict):
    status: Optional[Literal["open", "in_progress", "waiting_user", "resolved", "closed"]] = None
    assigned_to: Optional[str] = Field(default=None, max_length=40)
    priority: Optional[Literal["low", "medium", "high"]] = None
    reply: Optional[str] = Field(default=None, max_length=6000)


class FeedbackRequest(Strict):
    rating: int = Field(ge=1, le=5)
    message: str = Field(min_length=5, max_length=4000)
    page: Optional[str] = Field(default=None, max_length=200)
    module: Optional[str] = Field(default=None, max_length=80)


class SavedAnalysisRequest(Strict):
    name: str = Field(min_length=2, max_length=140)
    description: Optional[str] = Field(default=None, max_length=1000)
    view_state: dict[str, Any] = Field(default_factory=dict)


class SettingsPatchRequest(Strict):
    key: str = Field(min_length=2, max_length=80)
    value: Any
