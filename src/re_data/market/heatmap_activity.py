"""City-wide windowed sales + median PPSF per area (heatmap facts).

One pass over ``comp_records_by_area``. Shared dataset-max window so areas
are comparable. Counts and median only — no demand bands or share_pct.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from statistics import median

from re_data.ingest.parsers import norm
from re_data.market.aliases import _AREA_EMIRATE_MAP, resolve_area_value
from re_data.models.domain import DatasetSnapshot


@dataclass(frozen=True)
class HeatmapAreaFact:
    area: str
    label: str
    transaction_count: int
    median_ppsf: Decimal | None


@dataclass(frozen=True)
class HeatmapActivityResult:
    date_from: str | None
    date_to: str | None
    items: list[HeatmapAreaFact]


def compute_heatmap_activity(
    snapshot: DatasetSnapshot,
    *,
    emirate: str | None,
    lookback_days: int,
) -> HeatmapActivityResult:
    """Inclusive city window: end = dataset latest date, start = end − (days − 1)."""
    end_iso = snapshot.totals.latest_transaction_date
    if not end_iso:
        return HeatmapActivityResult(date_from=None, date_to=None, items=[])

    end_date = date.fromisoformat(end_iso)
    start_iso = (end_date - timedelta(days=lookback_days - 1)).isoformat()
    emirate_norm = norm(emirate) if emirate else None

    items: list[HeatmapAreaFact] = []
    for canonical, records in snapshot.comp_records_by_area.items():
        if not canonical:
            continue
        area_emirate = _AREA_EMIRATE_MAP.get(canonical, "dubai")
        if emirate_norm and area_emirate != emirate_norm:
            continue
        ppsf_values: list[Decimal] = []
        for rec in records:
            tx_date = rec.transaction_date
            if tx_date is None:
                continue
            if emirate_norm and rec.emirate and rec.emirate != emirate_norm:
                continue
            if start_iso <= tx_date <= end_iso:
                ppsf_values.append(rec.aed_per_sqft)
        count = len(ppsf_values)
        items.append(
            HeatmapAreaFact(
                area=resolve_area_value(canonical),
                label=canonical.title(),
                transaction_count=count,
                median_ppsf=median(ppsf_values) if count else None,
            )
        )

    items.sort(key=lambda row: (-row.transaction_count, row.label))
    return HeatmapActivityResult(date_from=start_iso, date_to=end_iso, items=items)
