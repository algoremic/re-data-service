"""Windowed DLD sales-activity counts for one area (ST-075 TASK-D).

Aggregation is O(n_area) via `filter_comps_unsorted` + `comp_records_by_area`.
Counts only — no `share_pct` (MI owns pie shares).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from re_data.market.comps import filter_comps_unsorted
from re_data.models.domain import DatasetSnapshot
from re_data.models.market_api import SalesActivitySlice

# Cite stats._MIN_WINDOW_RECORDS — same credibility gate; do not import the private name.
MIN_SALES_ACTIVITY_N = 10

WindowEndSource = Literal["area_max", "dataset_max"]


@dataclass(frozen=True)
class SalesActivityResult:
    transaction_count: int
    date_from: str | None
    date_to: str | None
    window_end_source: WindowEndSource | None
    slices: list[SalesActivitySlice]
    below_minimum_n: bool
    matched_emirate: str | None
    matched_area: str


def _empty_result(*, matched_area: str, matched_emirate: str | None) -> SalesActivityResult:
    return SalesActivityResult(
        transaction_count=0,
        date_from=None,
        date_to=None,
        window_end_source=None,
        slices=[],
        below_minimum_n=True,
        matched_emirate=matched_emirate,
        matched_area=matched_area,
    )


def compute_sales_activity(
    snapshot: DatasetSnapshot,
    *,
    area: str,
    emirate: str | None,
    lookback_days: int,
) -> SalesActivityResult:
    """Inclusive lookback window over area-keyed sale comps.

    Window end is the max dated matched row, else `totals.latest_transaction_date`.
    Unknown area (no matched rows) is an empty result — not a dataset-max window.
    """
    filtered = filter_comps_unsorted(
        snapshot,
        area=area,
        emirate=emirate,
        unit_type=None,
        bedrooms=0,
        building=None,
        relax=False,
    )
    matched = filtered.records
    matched_area = filtered.matched_scope.area
    matched_emirate = filtered.matched_scope.emirate

    if not matched:
        return _empty_result(matched_area=matched_area, matched_emirate=matched_emirate)

    area_max = max((r.transaction_date for r in matched if r.transaction_date), default=None)
    if area_max is not None:
        end_iso = area_max
        window_end_source: WindowEndSource = "area_max"
    elif snapshot.totals.latest_transaction_date:
        end_iso = snapshot.totals.latest_transaction_date
        window_end_source = "dataset_max"
    else:
        return _empty_result(matched_area=matched_area, matched_emirate=matched_emirate)

    end_date = date.fromisoformat(end_iso)
    start_iso = (end_date - timedelta(days=lookback_days - 1)).isoformat()

    counts: Counter[str] = Counter()
    for r in matched:
        tx_date = r.transaction_date
        if tx_date is None:
            continue
        if start_iso <= tx_date <= end_iso:
            counts[r.unit_type or "unknown"] += 1

    transaction_count = sum(counts.values())
    slices = [
        SalesActivitySlice(label=label, count=count)
        for label, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
    return SalesActivityResult(
        transaction_count=transaction_count,
        date_from=start_iso,
        date_to=end_iso,
        window_end_source=window_end_source,
        slices=slices,
        below_minimum_n=transaction_count < MIN_SALES_ACTIVITY_N,
        matched_emirate=matched_emirate,
        matched_area=matched_area,
    )
