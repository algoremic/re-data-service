from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

BenchmarkKey = tuple[str, str, str, str, int]
IndexKey = tuple[str, str]

DataState = Literal["complete", "degraded"]
FileStatus = Literal["loaded", "missing", "failed", "partial"]
DetectedSchema = Literal[
    "dld_transactions",
    "recent_transactions",
    "generic_comps",
    "residential_sale_index",
    "unknown",
]


@dataclass(frozen=True)
class CompRecord:
    """One accepted transaction row, retained alongside the grouped ppsf aggregates.

    Populated from the same parsed values used by `_accept_row` today — no
    re-calculation. See ADR-016 for the area-keyed index this record feeds.
    """

    transaction_date: str | None  # ISO YYYY-MM-DD; null when unparseable
    price_aed: Decimal
    size_sqft: Decimal
    aed_per_sqft: Decimal
    emirate: str  # normalized lowercase (key_tuple output)
    area: str  # normalized lowercase canonical
    building: str  # normalized; "" when absent
    unit_type: str  # normalized; "" when absent
    bedrooms: int  # 0 = unspecified
    transaction_id: str | None  # DLD schema only; null otherwise


@dataclass(frozen=True)
class BenchmarkStat:
    aed_per_sqft: Decimal
    sample_size: int
    last_updated: str | None = None
    source: str = "csv"
    confidence: float | None = None


@dataclass(frozen=True)
class IndexSnapshot:
    date: str
    segment: str
    horizon: str
    index_value: Decimal | None
    price_index_value: Decimal | None


@dataclass(frozen=True)
class SkipReasons:
    filtered_procedure: int = 0
    out_of_bounds_ppsf: int = 0
    unparseable: int = 0

    @property
    def rows_skipped(self) -> int:
        return self.filtered_procedure + self.out_of_bounds_ppsf + self.unparseable


@dataclass(frozen=True)
class FileIngestReport:
    path: str
    name: str
    status: FileStatus
    schema: DetectedSchema | None
    content_hash: str | None
    size_bytes: int | None
    rows_read: int
    rows_accepted: int
    rows_skipped: int
    skip_reasons: SkipReasons
    duplicate_content_hash_of: str | None


@dataclass(frozen=True)
class DuplicateHashGroup:
    content_hash: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class TotalsSummary:
    accepted_transaction_rows: int
    index_loaded: bool
    index_snapshot_count: int
    latest_transaction_date: str | None
    comp_record_count: int = 0  # ADR-016 — total CompRecords across all areas


@dataclass(frozen=True)
class DatasetSnapshot:
    version: str
    ingested_at: datetime
    state: DataState
    files: tuple[FileIngestReport, ...]
    transactions: dict[BenchmarkKey, tuple[Decimal, ...]]
    last_dates: dict[BenchmarkKey, str]
    index: dict[IndexKey, IndexSnapshot]
    totals: TotalsSummary
    duplicate_content_hashes: tuple[DuplicateHashGroup, ...]
    comp_records_by_area: dict[str, tuple[CompRecord, ...]] = field(  # ADR-016
        default_factory=dict
    )
