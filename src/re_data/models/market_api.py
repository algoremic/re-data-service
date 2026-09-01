"""Pydantic response models for the /v1/market/* endpoints.

All Decimal money fields are serialised as strings per the engineering contract.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer


class MatchedKey(BaseModel):
    emirate: str
    area: str
    building: str
    unit_type: str
    bedrooms: int


class AreaItem(BaseModel):
    value: str
    label: str
    emirate: str


class AreasListResponse(BaseModel):
    items: list[AreaItem]
    dataset_version: str
    data_state: Literal["complete", "degraded"]


class PpsfResponse(BaseModel):
    found: bool
    aed_per_sqft: Decimal | None = None
    sample_size: int = 0
    confidence: float | None = None
    last_updated: str | None = None
    source: str | None = None
    matched_key: MatchedKey | None = None
    dataset_version: str
    data_state: Literal["complete", "degraded"]

    @field_serializer("aed_per_sqft")
    def _ser_aed(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None


class CompItem(BaseModel):
    transaction_date: str | None = None
    price_aed: Decimal
    size_sqft: Decimal
    aed_per_sqft: Decimal
    emirate: str
    area: str
    building: str
    unit_type: str
    bedrooms: int
    transaction_id: str | None = None

    @field_serializer("price_aed", "size_sqft", "aed_per_sqft")
    def _ser_decimal(self, v: Decimal) -> str:
        return str(v)


class CompsMatchedScope(BaseModel):
    emirate: str | None = None
    area: str
    unit_type: str | None = None
    bedrooms: int = 0
    building: str | None = None
    relaxed: bool = False


class CompsResponse(BaseModel):
    total: int
    items: list[CompItem]
    matched_scope: CompsMatchedScope
    source: str = "DLD public transaction data"
    dataset_version: str
    data_state: Literal["complete", "degraded"]


class RealizedPathHorizon(BaseModel):
    horizon_months: Literal[6, 12, 24]
    status: Literal["ok", "immature", "below_minimum_n"]
    median_ppsf: Decimal | None = None
    sample_size: int | None = None

    @field_serializer("median_ppsf")
    def _ser_median(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None


class RealizedPathResponse(BaseModel):
    area: str
    sale_date: str
    anchor_date: str | None = None
    horizons: list[RealizedPathHorizon]
    disclaimer: str
    matched_scope: CompsMatchedScope
    dataset_version: str
    data_state: Literal["complete", "degraded"]


class DateRange(BaseModel):
    from_: str | None = Field(default=None, alias="from", serialization_alias="from")
    to: str | None = None

    model_config = ConfigDict(populate_by_name=True)


class AreaStatsResponse(BaseModel):
    ok: bool
    area: str
    emirate: str | None = None
    matched_scope: CompsMatchedScope | None = None
    transaction_count: int = 0
    median_ppsf: Decimal | None = None
    mean_ppsf: Decimal | None = None
    p25_ppsf: Decimal | None = None
    p75_ppsf: Decimal | None = None
    min_ppsf: Decimal | None = None
    max_ppsf: Decimal | None = None
    confidence: float | None = None
    date_range: DateRange | None = None
    yoy_available: bool = False
    yoy_median_ppsf_change_pct: Decimal | None = None
    message: str | None = None
    source: str = "DLD public transaction data"
    dataset_version: str
    data_state: Literal["complete", "degraded"]

    # DESIGN-ST-027-030 §4.2 — worst-avg appreciation growth fields (additive, ST-027).
    # `yoy_median_ppsf_change_pct` above is unchanged and MUST NOT be used as a
    # growth default (advisor/display only) — see `conservative_growth_annual_pct`.
    yoy_p25_ppsf_change_pct: Decimal | None = None
    cagr_3y_lower_annual_pct: Decimal | None = None
    conservative_growth_annual_pct: Decimal | None = None
    growth_suggestion_available: bool = False
    growth_method: str | None = None

    @field_serializer(
        "median_ppsf",
        "mean_ppsf",
        "p25_ppsf",
        "p75_ppsf",
        "min_ppsf",
        "max_ppsf",
        "yoy_median_ppsf_change_pct",
        "yoy_p25_ppsf_change_pct",
        "cagr_3y_lower_annual_pct",
        "conservative_growth_annual_pct",
    )
    def _ser_decimal(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None


class IndexLatestResponse(BaseModel):
    ok: bool
    date: str | None = None
    segment: str | None = None
    horizon: str | None = None
    index_value: Decimal | None = None
    price_index_value: Decimal | None = None
    message: str | None = None
    dataset_version: str
    data_state: Literal["complete", "degraded"]

    @field_serializer("index_value", "price_index_value")
    def _ser_decimal(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None


class SalesActivitySlice(BaseModel):
    label: str
    count: int


class SalesActivityMatchedScope(BaseModel):
    emirate: str | None = None
    area: str


class HeatmapAreaFact(BaseModel):
    area: str
    label: str
    transaction_count: int
    median_ppsf: Decimal | None = None

    @field_serializer("median_ppsf")
    def _ser_ppsf(self, v: Decimal | None) -> str | None:
        return str(v) if v is not None else None


class HeatmapActivityResponse(BaseModel):
    date_range: DateRange | None = None
    items: list[HeatmapAreaFact]
    data_state: Literal["complete", "degraded"]
    source: str = "DLD public transaction data"


class SalesActivityResponse(BaseModel):
    transaction_count: int
    date_range: DateRange | None = None
    window_end_source: Literal["area_max", "dataset_max"] | None = None
    slices: list[SalesActivitySlice]
    below_minimum_n: bool
    dataset_version: str
    data_state: Literal["complete", "degraded"]
    source: str = "DLD public transaction data"
    matched_scope: SalesActivityMatchedScope
