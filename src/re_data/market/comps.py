"""Comparable transactions — area-keyed filter/sort per ADR-016.

`/v1/market/comps`, `/v1/market/stats/{area}`, and `/v1/market/sales-activity`
share this module so the area-keyed lookup (`snapshot.comp_records_by_area`) is
centralised in one place. Sales-activity uses `filter_comps_unsorted` — matching
without the date-desc sort that exists only for comps pagination.
"""

from __future__ import annotations

from dataclasses import dataclass

from re_data.ingest.parsers import norm
from re_data.market.aliases import _AREA_ALIASES, _UNIT_TYPE_ALIASES
from re_data.models.domain import CompRecord, DatasetSnapshot
from re_data.models.market_api import CompsMatchedScope


@dataclass(frozen=True)
class CompsFilterResult:
    records: list[CompRecord]  # sorted, pre-pagination
    total: int
    matched_scope: CompsMatchedScope


def _sort_key(r: CompRecord) -> tuple[bool, str, float]:
    """Sort by transaction_date DESC (nulls last), tie-break aed_per_sqft DESC."""
    return (r.transaction_date is None, r.transaction_date or "", -float(r.aed_per_sqft))


def _matches(
    r: CompRecord,
    *,
    emirate_norm: str | None,
    ut_set: set[str] | None,
    bedrooms: int,
    building_norm: str | None,
) -> bool:
    if emirate_norm is not None and r.emirate != emirate_norm:
        return False
    if ut_set is not None and r.unit_type not in ut_set:
        return False
    if bedrooms != 0 and r.bedrooms != bedrooms:
        return False
    if building_norm is not None and r.building != building_norm:
        return False
    return True


def filter_comps_unsorted(
    snapshot: DatasetSnapshot,
    *,
    area: str,
    emirate: str | None,
    unit_type: str | None,
    bedrooms: int,
    building: str | None,
    relax: bool,
) -> CompsFilterResult:
    """Area-keyed match (`norm`, aliases, emirate, sale-only records). No sort.

    Uses `comp_records_by_area.get` only — never iterates every area.
    """
    area_norm = norm(area)
    area_canon = _AREA_ALIASES.get(area_norm, area_norm)

    # ADR-016: area-keyed lookup — O(area_records), not a full-dataset scan.
    candidates = snapshot.comp_records_by_area.get(area_canon, ())

    emirate_norm = norm(emirate) if emirate else None
    ut_norm = norm(unit_type) if unit_type else None
    ut_set: set[str] | None = set(_UNIT_TYPE_ALIASES.get(ut_norm, [ut_norm])) if ut_norm else None
    building_norm = norm(building) if building else None

    def _run(*, use_building: bool, use_bedrooms: bool) -> list[CompRecord]:
        return [
            r
            for r in candidates
            if _matches(
                r,
                emirate_norm=emirate_norm,
                ut_set=ut_set,
                bedrooms=bedrooms if use_bedrooms else 0,
                building_norm=building_norm if use_building else None,
            )
        ]

    matched = _run(use_building=True, use_bedrooms=True)
    relaxed = False
    effective_building_norm = building_norm
    effective_bedrooms = bedrooms

    if relax and not matched:
        # Progressive relaxation: drop building -> drop bedrooms -> area+unit_type only.
        for use_building, use_bedrooms in ((False, True), (False, False)):
            widened = _run(use_building=use_building, use_bedrooms=use_bedrooms)
            if widened:
                matched = widened
                relaxed = True
                effective_building_norm = building_norm if use_building else None
                effective_bedrooms = bedrooms if use_bedrooms else 0
                break

    matched_scope = CompsMatchedScope(
        emirate=emirate_norm,
        area=area_canon,
        unit_type=ut_norm,
        bedrooms=effective_bedrooms,
        building=effective_building_norm or "",
        relaxed=relaxed,
    )

    return CompsFilterResult(records=matched, total=len(matched), matched_scope=matched_scope)


def filter_and_sort_comps(
    snapshot: DatasetSnapshot,
    *,
    area: str,
    emirate: str | None,
    unit_type: str | None,
    bedrooms: int,
    building: str | None,
    relax: bool,
) -> CompsFilterResult:
    result = filter_comps_unsorted(
        snapshot,
        area=area,
        emirate=emirate,
        unit_type=unit_type,
        bedrooms=bedrooms,
        building=building,
        relax=relax,
    )
    result.records.sort(key=_sort_key)
    return result
