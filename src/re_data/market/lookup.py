"""PPSF lookup with progressive-relaxation fallback chain.

Stats (_median, _calc_stat) and the key-expansion chain are copied from
realestate-core-service/benchmarks.py so that parity tests can assert
identical median/confidence values on shared CSV fixtures.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

from re_data.ingest.parsers import key_tuple, norm
from re_data.market.aliases import _AREA_ALIASES, _UNIT_TYPE_ALIASES
from re_data.models.domain import BenchmarkKey, BenchmarkStat, DatasetSnapshot


@dataclass(frozen=True)
class StatResult:
    median: Decimal
    n: int
    confidence: float


@dataclass(frozen=True)
class PpsfLookupResult:
    stat: BenchmarkStat
    matched_key: BenchmarkKey


def _median(vals: list[Decimal]) -> Decimal:
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return Decimal("0")
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / Decimal("2")


def _calc_stat(vals: tuple[Decimal, ...] | list[Decimal]) -> StatResult:
    """Compute trimmed median and confidence, mirroring benchmarks.py._calc_stat."""
    n = len(vals)
    if n == 0:
        return StatResult(median=Decimal("0"), n=0, confidence=0.0)

    s = sorted(vals)
    if n >= 10:
        k = int(n * 0.10)
        s2 = s[k : n - k] if (n - 2 * k) >= 1 else s
    else:
        s2 = s

    med = _median(s2)
    conf = float(min(1.0, math.log(max(n, 1) + 1) / math.log(101)))
    return StatResult(median=med, n=n, confidence=conf)


def _area_level_lookup(
    snapshot: DatasetSnapshot,
    *,
    emirate_norm: str,
    area_candidates: list[str],
    ut_norm: str,
    ut_candidates: list[str],
    bedrooms: int,
) -> PpsfLookupResult | None:
    """Aggregate PPSF for an area using the area-keyed CompRecord index (ADR-016).

    Uses snapshot.comp_records_by_area for O(area_sales) lookup instead of an
    O(all_keys) full transaction-dict scan. DLD ingest stores one CompRecord per
    accepted row — keyed by canonical area — so area-only MI queries pay only for
    the records belonging to that area.

    When the caller supplies unit_type / bedrooms, try the specific filter first,
    then progressively relax (drop bedrooms → drop unit_type) so calculator
    autofill still returns an area market average when the selected typology has
    no rows (common for villa-heavy areas queried as apartment).

    Tries each non-empty area alias candidate until one has records.
    Matched key uses building="" / unit_type="" / bedrooms=0 to signal area-level
    aggregation. Returns None only when no area candidate has any sales.
    """
    areas = [a for a in area_candidates if a]
    if not areas:
        return None

    ut_match_set = {u for u in ut_candidates if u}
    # Progressive relaxation: specific → drop one dimension → area-wide.
    attempts: list[tuple[bool, bool]] = []
    if ut_norm and bedrooms > 0:
        attempts.append((True, True))
    if ut_norm:
        attempts.append((True, False))
    if bedrooms > 0:
        attempts.append((False, True))
    attempts.append((False, False))
    # Preserve order while removing duplicates from overlapping branches.
    seen_attempts: set[tuple[bool, bool]] = set()
    unique_attempts: list[tuple[bool, bool]] = []
    for attempt in attempts:
        if attempt not in seen_attempts:
            seen_attempts.add(attempt)
            unique_attempts.append(attempt)

    for matched_area in areas:
        records = snapshot.comp_records_by_area.get(matched_area, ())
        if not records:
            continue

        for filter_ut, filter_bedrooms in unique_attempts:
            collected: list[Decimal] = []
            latest_date: str | None = None

            for r in records:
                if r.emirate != emirate_norm:
                    continue
                if filter_ut and r.unit_type not in ut_match_set:
                    continue
                # bedrooms=0 CompRecords are "unspecified" — always include when a
                # bedrooms filter is active so partially-attributed rows are not lost.
                if filter_bedrooms and r.bedrooms not in (0, bedrooms):
                    continue

                collected.append(r.aed_per_sqft)
                if r.transaction_date and (
                    latest_date is None or r.transaction_date > latest_date
                ):
                    latest_date = r.transaction_date

            if not collected:
                continue

            result = _calc_stat(collected)
            matched_key = key_tuple(emirate_norm, matched_area, "", "", 0)
            stat = BenchmarkStat(
                aed_per_sqft=result.median,
                sample_size=result.n,
                last_updated=latest_date,
                source="csv",
                confidence=result.confidence,
            )
            return PpsfLookupResult(stat=stat, matched_key=matched_key)

    return None


def lookup_ppsf(
    snapshot: DatasetSnapshot,
    *,
    emirate: str | None,
    area: str | None,
    building: str | None,
    unit_type: str | None,
    bedrooms: int | None,
) -> PpsfLookupResult | None:
    """Look up PPSF for the given parameters.

    When *building* is omitted/blank: aggregates PPSF values from ALL matching
    keys for the area (regardless of stored building name) so that real DLD data
    keyed by project/building name is included in MI area-level summaries.

    When *building* is provided: uses the existing exact-key then progressive-
    relaxation fallback chain (specific building → empty building → relax bedrooms
    → relax unit_type → global fallback).
    """
    ut_norm = norm(unit_type or "")
    ut_candidates = list(
        dict.fromkeys(_UNIT_TYPE_ALIASES.get(ut_norm, [ut_norm] if ut_norm else []) + [""])
    )

    area_norm = norm(area or "")
    area_expanded = _AREA_ALIASES.get(area_norm, area_norm)
    area_candidates = list(dict.fromkeys([area_expanded, area_norm, ""]))

    bedrooms_norm = int(bedrooms or 0)
    building_norm = norm(building or "")
    emirate_norm = norm(emirate or "dubai")

    # Area-only mode: no building specified — scan all keys for this area and
    # aggregate, so building-level ingest rows feed MI area benchmarks.
    if not building_norm:
        return _area_level_lookup(
            snapshot,
            emirate_norm=emirate_norm,
            area_candidates=area_candidates,
            ut_norm=ut_norm,
            ut_candidates=ut_candidates,
            bedrooms=bedrooms_norm,
        )

    # Building-specific: keep exact key + progressive-relaxation chain.
    candidates: list[BenchmarkKey] = []
    for ar in area_candidates:
        for ut in ut_candidates:
            candidates += [
                key_tuple(emirate, ar, building, ut, bedrooms),
                key_tuple(emirate, ar, "", ut, bedrooms),
                key_tuple(emirate, ar, "", ut, 0),
            ]
    candidates.append(key_tuple(emirate, "", "", "", 0))

    seen: set[BenchmarkKey] = set()
    for ck in candidates:
        if ck in seen:
            continue
        seen.add(ck)
        values = snapshot.transactions.get(ck)
        if values:
            result = _calc_stat(values)
            stat = BenchmarkStat(
                aed_per_sqft=result.median,
                sample_size=result.n,
                last_updated=snapshot.last_dates.get(ck),
                source="csv",
                confidence=result.confidence,
            )
            return PpsfLookupResult(stat=stat, matched_key=ck)

    return None
