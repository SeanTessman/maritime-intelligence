"""Pydantic response models for the MIADP API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

MMSI_PATTERN = r"^[0-9]{9}$"


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int = Field(description="total matching rows (before pagination)")
    limit: int
    offset: int


class ErrorResponse(BaseModel):
    error: str
    detail: Any | None = None
    request_id: str | None = None


class Health(BaseModel):
    status: str
    database: str
    database_ok: bool
    version: str
    disclaimer: str


class Vessel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    mmsi: str
    vessel_name: str | None = None
    imo: str | None = None
    call_sign: str | None = None
    vessel_type: int | None = None
    vessel_type_group: str | None = None
    length_m: float | None = None
    width_m: float | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    is_synthetic: bool = False
    n_positions: int | None = None


class Position(BaseModel):
    position_id: int
    mmsi: str
    base_datetime: datetime
    lat: float
    lon: float
    sog: float | None = None
    cog: float | None = None
    heading: float | None = None
    nav_status: int | None = None
    quality_flags: int = 0
    quality_flag_names: list[str] = []


class TrackFeatures(BaseModel):
    track_id: str
    mmsi: str
    track_date: date
    vessel_type_group: str
    features: dict[str, float | None]
    anomaly_score: float | None = None
    score_split: str | None = None


class Anomaly(BaseModel):
    event_id: int
    run_key: str
    detector: str
    anomaly_type: str
    mmsi: str
    event_time: datetime
    end_time: datetime | None = None
    lat: float | None = None
    lon: float | None = None
    score: float | None = None
    explanation: str
    quality_notes: str | None = None
    is_synthetic: bool = False


class AnomalyDetail(Anomaly):
    rule_flags: dict[str, Any] | None = None
    features: dict[str, Any] | None = None
    supporting_position_ids: list[int] = []
    supporting_positions: list[Position] = []
    model_version: str | None = None


class Port(BaseModel):
    port_id: int
    wpi_number: int | None = None
    port_name: str
    country: str | None = None
    harbor_size: str | None = None
    lat: float
    lon: float


class NetworkEdge(BaseModel):
    source: str
    target: str
    relationship_type: str
    evidence_table: str
    evidence_ids: list[int]
    evidence_count: int
    start_time: str | None = None
    end_time: str | None = None
    source_dataset: str
    quality: dict[str, Any]


class NetworkNode(BaseModel):
    id: str
    node_type: str
    label: str | None = None


class VesselNetwork(BaseModel):
    center: str
    nodes: list[NetworkNode]
    edges: list[NetworkEdge]
    note: str


class IngestionRun(BaseModel):
    run_id: int
    file_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    rows_raw: int
    rows_in_bbox: int
    rows_accepted: int
    rows_rejected: int
    rows_duplicate: int
    rows_inserted: int
    error_message: str | None = None


class Case(BaseModel):
    case_id: int
    title: str
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    evidence_items: int
