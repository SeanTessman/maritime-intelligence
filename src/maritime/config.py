"""Central configuration loaded from environment variables (and an optional .env file).

Everything that could reasonably change between runs (database location, study
window, study region, thresholds file) lives here rather than in code.
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class BoundingBox(BaseModel):
    """A WGS84 latitude/longitude box. Longitudes must not cross the antimeridian."""

    min_lat: float = Field(ge=-90, le=90)
    max_lat: float = Field(ge=-90, le=90)
    min_lon: float = Field(ge=-180, le=180)
    max_lon: float = Field(ge=-180, le=180)

    @model_validator(mode="after")
    def _ordered(self) -> BoundingBox:
        if self.min_lat >= self.max_lat:
            raise ValueError("min_lat must be < max_lat")
        if self.min_lon >= self.max_lon:
            raise ValueError("min_lon must be < max_lon (antimeridian-crossing boxes unsupported)")
        return self

    def contains(self, lat: float, lon: float) -> bool:
        return self.min_lat <= lat <= self.max_lat and self.min_lon <= lon <= self.max_lon

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.min_lat, self.max_lat, self.min_lon, self.max_lon)

    @property
    def key(self) -> str:
        """Stable string used to tie ingestion runs to the region they filtered on."""
        return f"{self.min_lat:.4f},{self.max_lat:.4f},{self.min_lon:.4f},{self.max_lon:.4f}"


NY_HARBOR = BoundingBox(min_lat=40.3, max_lat=41.1, min_lon=-74.4, max_lon=-73.4)


class Settings(BaseSettings):
    """Runtime settings. Values come from env vars prefixed MIADP_ (or POSTGRES_* for the DB)."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    postgres_host: str = "127.0.0.1"
    postgres_port: int = 5432
    postgres_db: str = "miadp"
    postgres_user: str = "miadp"
    postgres_password: str = Field(default="", repr=False)
    database_url: str | None = Field(default=None, repr=False)

    miadp_study_start: date = date(2023, 1, 9)
    miadp_study_end: date = date(2023, 1, 15)
    miadp_train_end: date = date(2023, 1, 13)
    miadp_bbox_min_lat: float = NY_HARBOR.min_lat
    miadp_bbox_max_lat: float = NY_HARBOR.max_lat
    miadp_bbox_min_lon: float = NY_HARBOR.min_lon
    miadp_bbox_max_lon: float = NY_HARBOR.max_lon

    miadp_ais_base_url: str = "https://coast.noaa.gov/htdata/CMSP/AISDataHandler"
    miadp_chunk_size: int = 250_000
    miadp_data_dir: Path = PROJECT_ROOT / "data"
    miadp_models_dir: Path = PROJECT_ROOT / "models"
    miadp_thresholds_file: Path = PROJECT_ROOT / "config" / "thresholds.yaml"
    miadp_log_level: str = "INFO"

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        from sqlalchemy.engine import URL

        return URL.create(
            "postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password or None,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)

    @property
    def bbox(self) -> BoundingBox:
        return BoundingBox(
            min_lat=self.miadp_bbox_min_lat,
            max_lat=self.miadp_bbox_max_lat,
            min_lon=self.miadp_bbox_min_lon,
            max_lon=self.miadp_bbox_max_lon,
        )

    @property
    def raw_dir(self) -> Path:
        return self.miadp_data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.miadp_data_dir / "processed"

    @property
    def reference_dir(self) -> Path:
        return self.miadp_data_dir / "reference"

    @property
    def synthetic_dir(self) -> Path:
        return self.miadp_data_dir / "synthetic"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
