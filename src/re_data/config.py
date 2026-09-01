from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SQM_TO_SQFT = Decimal("10.7639104167")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    benchmarks_csv_paths: str = Field(default="", validation_alias="BENCHMARKS_CSV_PATHS")
    index_csv_path: str = Field(
        default="/data/Residential_Sale_Index.csv",
        validation_alias="INDEX_CSV_PATH",
    )
    dld_area_unit: str = Field(default="sqm", validation_alias="DLD_AREA_UNIT")
    ppsf_min: Decimal = Field(default=Decimal("100"), validation_alias="PPSF_MIN")
    ppsf_max: Decimal = Field(default=Decimal("20000"), validation_alias="PPSF_MAX")
    port: int = Field(default=8001, validation_alias="PORT")
    app_env: str = Field(default="local", validation_alias="APP_ENV")
    redis_url: str = Field(default="", validation_alias="REDIS_URL")
    market_cache_ttl_s: int = Field(default=900, validation_alias="MARKET_CACHE_TTL_S")
    mongo_uri: str = Field(default="", validation_alias="MONGO_URI")
    mongo_db: str = Field(default="RECALMI", validation_alias="MONGO_DB")
    mongo_collection_ready: str = Field(default="ready", validation_alias="MONGO_COLLECTION_READY")
    mongo_collection_offplan: str = Field(
        default="off-plan", validation_alias="MONGO_COLLECTION_OFFPLAN"
    )
    # ST-053: admin reload auth (fail-closed when empty)
    data_service_token: str = Field(default="", validation_alias="DATA_SERVICE_TOKEN")
    # ST-053: max seconds for one reload ingest before timeout; must be > 0
    max_reload_s: float = Field(default=300.0, validation_alias="MAX_RELOAD_S", gt=0)
    # SEC-ENG-ST-053 / AVAIL-01: min seconds between reload attempts (0 disables)
    admin_reload_cooldown_s: float = Field(
        default=5.0, validation_alias="ADMIN_RELOAD_COOLDOWN_S", ge=0
    )

    @field_validator("data_service_token", mode="before")
    @classmethod
    def _norm_token(cls, v: object) -> str:
        return str(v or "").strip()

    @field_validator("data_service_token", mode="after")
    @classmethod
    def _check_token_len(cls, v: str) -> str:
        if len(v) > 512:
            raise ValueError("DATA_SERVICE_TOKEN must be at most 512 characters")
        return v

    @field_validator("dld_area_unit", mode="before")
    @classmethod
    def _norm_unit(cls, v: object) -> str:
        return str(v or "sqm").strip().lower()

    @field_validator("ppsf_min", "ppsf_max", mode="before")
    @classmethod
    def _to_decimal(cls, v: object) -> Decimal:
        if isinstance(v, Decimal):
            return v
        return Decimal(str(v).strip())

    @field_validator("redis_url", mode="before")
    @classmethod
    def _norm_optional_url(cls, v: object) -> str:
        return str(v or "").strip()

    def transaction_paths(self) -> list[Path]:
        raw = self.benchmarks_csv_paths.strip()
        if not raw:
            return []
        paths: list[Path] = []
        for part in raw.split(";"):
            part = part.strip().strip('"')
            if part:
                paths.append(Path(part))
        return paths

    def index_path(self) -> Path | None:
        raw = self.index_csv_path.strip().strip('"')
        if not raw:
            return None
        return Path(raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
