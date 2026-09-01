"""Post-sale area median PPSF windows (T+6/12/24) — ADR-016 area-keyed lookup."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from re_data.market.comps import filter_comps_unsorted
from re_data.market.lookup import _median
from re_data.models.domain import CompRecord, DatasetSnapshot
from re_data.models.market_api import CompsMatchedScope

REALIZED_HORIZONS_MONTHS = (6, 12, 24)
_MIN_SAMPLE_SIZE = 10

_REALIZED_DISCLAIMER = (
    "Outcomes are area/segment median PPSF after the sale date, "
    "not this unit's resale price."
)

REALIZED_DISCLAIMER = _REALIZED_DISCLAIMER


@dataclass(frozen=True)
class RealizedHorizonResult:
    horizon_months: int
    status: str  # ok | immature | below_minimum_n
    median_ppsf: Decimal | None
    sample_size: int | None


@dataclass(frozen=True)
class RealizedPathResult:
    area: str
    sale_date: str
    anchor_date: str | None
    horizons: list[RealizedHorizonResult]
    matched_scope: CompsMatchedScope
    dataset_version: str
    data_state: str


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _anchor_date(records: list[CompRecord]) -> str | None:
    dated = [r.transaction_date for r in records if r.transaction_date]
    return max(dated) if dated else None


def compute_realized_path(
    snapshot: DatasetSnapshot,
    *,
    area: str,
    sale_date: str,
    emirate: str | None,
    unit_type: str | None,
    bedrooms: int,
    relax: bool,
) -> RealizedPathResult:
    """Median PPSF in (sale_date, sale_date+h] per horizon, area-keyed only."""
    filter_result = filter_comps_unsorted(
        snapshot,
        area=area,
        emirate=emirate,
        unit_type=unit_type,
        bedrooms=bedrooms,
        building=None,
        relax=relax,
    )
    anchor = _anchor_date(filter_result.records)
    sale = date.fromisoformat(sale_date)
    anchor_d = date.fromisoformat(anchor) if anchor else None

    horizons: list[RealizedHorizonResult] = []
    for h in REALIZED_HORIZONS_MONTHS:
        window_end = _add_months(sale, h)
        if anchor_d is None or window_end > anchor_d:
            horizons.append(
                RealizedHorizonResult(
                    horizon_months=h,
                    status="immature",
                    median_ppsf=None,
                    sample_size=None,
                )
            )
            continue

        window_end_iso = window_end.isoformat()
        window_sales = [
            r
            for r in filter_result.records
            if r.transaction_date
            and sale_date < r.transaction_date <= window_end_iso
        ]
        n = len(window_sales)
        if n < _MIN_SAMPLE_SIZE:
            horizons.append(
                RealizedHorizonResult(
                    horizon_months=h,
                    status="below_minimum_n",
                    median_ppsf=None,
                    sample_size=n,
                )
            )
            continue

        med = _median([r.aed_per_sqft for r in window_sales])
        horizons.append(
            RealizedHorizonResult(
                horizon_months=h,
                status="ok",
                median_ppsf=med,
                sample_size=n,
            )
        )

    return RealizedPathResult(
        area=filter_result.matched_scope.area,
        sale_date=sale_date,
        anchor_date=anchor,
        horizons=horizons,
        matched_scope=filter_result.matched_scope,
        dataset_version=snapshot.version,
        data_state=snapshot.state,
    )


__all__ = [
    "REALIZED_DISCLAIMER",
    "REALIZED_HORIZONS_MONTHS",
    "RealizedHorizonResult",
    "RealizedPathResult",
    "compute_realized_path",
]
