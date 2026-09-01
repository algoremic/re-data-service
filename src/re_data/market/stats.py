"""Area-level distribution statistics computed from filtered comp records.

Delegates area-keyed filtering to `market/comps.py` (ADR-016 lookup path) and
applies the §6 DESIGN-ST-005 trim / percentile / YoY-gate computation on top.

DESIGN-ST-027-030 §4.1 adds the **worst-avg appreciation** growth candidates
(G1 p25-YoY, G2 3y-CAGR-lower) on top of the same credibility gates used for
`yoy_median_ppsf_change_pct`. These candidates are DLD-SSOT only — no web/Tavily
input ever reaches this module.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from re_data.market.comps import filter_and_sort_comps
from re_data.models.domain import CompRecord, DatasetSnapshot
from re_data.models.market_api import CompsMatchedScope

_YOY_WINDOW_DAYS = 364
_YOY_PRIOR_START_DAYS = 729
_YOY_PRIOR_END_DAYS = 365
_MIN_WINDOW_RECORDS = 10
_MIN_DATED_COVERAGE = 0.5
_MIN_DISTINCT_DATES = 2

# DESIGN-ST-027-030 §4.1 — G2 3y-CAGR-lower gates.
_MIN_YEAR_RECORDS = 10
_MIN_CAGR_SPAN_YEARS = 2  # consecutive credible years must span >= 24 months

# DESIGN-ST-027-030 §4.1 — clamp: outside this band a candidate is not credible.
_GROWTH_CLAMP_ABS_PCT = Decimal("50")

_GROWTH_METHOD_LABEL = "min(p25_yoy, cagr_3y_lower)"


@dataclass
class AreaStatsResult:
    ok: bool
    area: str
    emirate: str | None
    matched_scope: CompsMatchedScope
    transaction_count: int
    median_ppsf: Decimal | None
    mean_ppsf: Decimal | None
    p25_ppsf: Decimal | None
    p75_ppsf: Decimal | None
    min_ppsf: Decimal | None
    max_ppsf: Decimal | None
    confidence: float | None
    date_from: str | None
    date_to: str | None
    yoy_available: bool
    yoy_pct: Decimal | None
    message: str | None
    # DESIGN-ST-027-030 §4.1 — worst-avg growth candidates (additive, DLD SSOT only).
    yoy_p25_pct: Decimal | None
    cagr_3y_lower_pct: Decimal | None
    conservative_growth_pct: Decimal | None
    growth_suggestion_available: bool
    growth_method: str | None


def _trimmed(ppsf_values: list[Decimal]) -> list[Decimal]:
    n = len(ppsf_values)
    s = sorted(ppsf_values)
    if n >= 10:
        k = int(n * 0.10)
        return s[k : n - k] if (n - 2 * k) >= 1 else s
    return s


def _quantile_decimal(sorted_vals: list[Decimal], q_index: int) -> Decimal:
    """Wrap `statistics.quantiles` float output back into Decimal (§6.2 note)."""
    floats = [float(v) for v in sorted_vals]
    if len(floats) == 1:
        return sorted_vals[0]
    q = statistics.quantiles(floats, n=4, method="inclusive")[q_index]
    return Decimal(str(round(q, 10)))


def _yoy_windows(
    records: list[CompRecord], anchor: str
) -> tuple[list[CompRecord], list[CompRecord]] | None:
    """Shared 364d current / prior window selection + credibility gates.

    Used by both the median YoY (`yoy_median_ppsf_change_pct`, unchanged) and the
    p25 YoY growth candidate (G1) — same gates, different aggregator (§4.1).
    """
    anchor_date = date.fromisoformat(anchor)
    current_start = anchor_date - timedelta(days=_YOY_WINDOW_DAYS)
    prior_start = anchor_date - timedelta(days=_YOY_PRIOR_START_DAYS)
    prior_end = anchor_date - timedelta(days=_YOY_PRIOR_END_DAYS)

    dated = [r for r in records if r.transaction_date]
    if not records or (len(dated) / len(records)) < _MIN_DATED_COVERAGE:
        return None
    if len({r.transaction_date for r in dated}) < _MIN_DISTINCT_DATES:
        return None

    current = [
        r
        for r in dated
        if current_start.isoformat() <= (r.transaction_date or "") <= anchor_date.isoformat()
    ]
    prior = [
        r
        for r in dated
        if prior_start.isoformat() <= (r.transaction_date or "") <= prior_end.isoformat()
    ]

    if len(current) < _MIN_WINDOW_RECORDS or len(prior) < _MIN_WINDOW_RECORDS:
        return None

    return current, prior


def _yoy(records: list[CompRecord], anchor: str) -> tuple[bool, Decimal | None]:
    windows = _yoy_windows(records, anchor)
    if windows is None:
        return False, None
    current, prior = windows

    median_current = statistics.median([r.aed_per_sqft for r in current])
    median_prior = statistics.median([r.aed_per_sqft for r in prior])
    if median_prior == 0:
        return False, None

    pct = (median_current - median_prior) / median_prior * Decimal("100")
    return True, Decimal(str(round(float(pct), 2)))


def _yoy_p25(records: list[CompRecord], anchor: str) -> tuple[bool, Decimal | None]:
    """G1 — p25 YoY: identical windows/gates as `_yoy`, p25 PPSF instead of median."""
    windows = _yoy_windows(records, anchor)
    if windows is None:
        return False, None
    current, prior = windows

    p25_current = _quantile_decimal(sorted(r.aed_per_sqft for r in current), 0)
    p25_prior = _quantile_decimal(sorted(r.aed_per_sqft for r in prior), 0)
    if p25_prior == 0:
        return False, None

    pct = (p25_current - p25_prior) / p25_prior * Decimal("100")
    return True, Decimal(str(round(float(pct), 2)))


def _yearly_p25_buckets(records: list[CompRecord]) -> dict[int, Decimal]:
    """Group dated records by calendar year; p25 PPSF for years with >= _MIN_YEAR_RECORDS."""
    by_year: dict[int, list[Decimal]] = defaultdict(list)
    for r in records:
        if not r.transaction_date:
            continue
        by_year[date.fromisoformat(r.transaction_date).year].append(r.aed_per_sqft)

    return {
        year: _quantile_decimal(sorted(vals), 0)
        for year, vals in by_year.items()
        if len(vals) >= _MIN_YEAR_RECORDS
    }


def _longest_consecutive_run(years: list[int]) -> list[int]:
    """Longest run of consecutive calendar years within a sorted, deduped year list."""
    best: list[int] = []
    current: list[int] = []
    for y in years:
        if current and current[-1] == y - 1:
            current.append(y)
        else:
            current = [y]
        if len(current) > len(best):
            best = current
    return best


def _cagr_3y_lower(records: list[CompRecord]) -> Decimal | None:
    """G2 — 3y CAGR lower bound (§4.1).

    Buckets dated records by calendar year, keeps years with >= _MIN_YEAR_RECORDS
    records (credible), requires >= 2 *consecutive* credible years spanning >= 24
    months, then returns the more pessimistic of:
      - CAGR between the earliest and latest credible-year p25 PPSF
      - the worst (minimum) single year-on-year change among adjacent credible years
    """
    credible_years = _yearly_p25_buckets(records)
    if len(credible_years) < 2:
        return None

    run = _longest_consecutive_run(sorted(credible_years))
    if len(run) < 2 or (run[-1] - run[0]) < _MIN_CAGR_SPAN_YEARS:
        return None

    p25_start = credible_years[run[0]]
    p25_end = credible_years[run[-1]]
    if p25_start <= 0:
        return None

    span_years = run[-1] - run[0]
    cagr_pct = Decimal(str(round((float(p25_end / p25_start) ** (1 / span_years) - 1) * 100, 2)))

    yoy_deltas = [
        (credible_years[b] - credible_years[a]) / credible_years[a] * Decimal("100")
        for a, b in zip(run, run[1:])
        if credible_years[a] > 0
    ]
    if not yoy_deltas:
        return cagr_pct

    yoy_min_pct = Decimal(str(round(float(min(yoy_deltas)), 2)))
    return min(cagr_pct, yoy_min_pct)


def _is_credible_rate(pct: Decimal | None) -> bool:
    return pct is not None and -_GROWTH_CLAMP_ABS_PCT <= pct <= _GROWTH_CLAMP_ABS_PCT


def _conservative_growth(
    yoy_p25_pct: Decimal | None, cagr_3y_lower_pct: Decimal | None
) -> tuple[Decimal | None, bool, str | None]:
    """Selected worst-avg default = min(credible G1, credible G2) (§4.1). Never median/p75."""
    candidates: list[Decimal] = [
        p for p in (yoy_p25_pct, cagr_3y_lower_pct) if p is not None and _is_credible_rate(p)
    ]
    if not candidates:
        return None, False, None
    return min(candidates), True, _GROWTH_METHOD_LABEL


def compute_area_stats(
    snapshot: DatasetSnapshot,
    *,
    area: str,
    emirate: str | None,
    unit_type: str | None,
    bedrooms: int,
    relax: bool,
) -> AreaStatsResult:
    filter_result = filter_and_sort_comps(
        snapshot,
        area=area,
        emirate=emirate,
        unit_type=unit_type,
        bedrooms=bedrooms,
        building=None,
        relax=relax,
    )
    records = filter_result.records
    matched_scope = filter_result.matched_scope
    n = len(records)

    if n == 0:
        return AreaStatsResult(
            ok=False,
            area=matched_scope.area,
            emirate=matched_scope.emirate,
            matched_scope=matched_scope,
            transaction_count=0,
            median_ppsf=None,
            mean_ppsf=None,
            p25_ppsf=None,
            p75_ppsf=None,
            min_ppsf=None,
            max_ppsf=None,
            confidence=None,
            date_from=None,
            date_to=None,
            yoy_available=False,
            yoy_pct=None,
            message="No transaction data for this area and filter combination.",
            yoy_p25_pct=None,
            cagr_3y_lower_pct=None,
            conservative_growth_pct=None,
            growth_suggestion_available=False,
            growth_method=None,
        )

    ppsf_values = [r.aed_per_sqft for r in records]
    trimmed = _trimmed(ppsf_values)

    if len(trimmed) == 1:
        median_ppsf = mean_ppsf = p25_ppsf = p75_ppsf = min_ppsf = max_ppsf = trimmed[0]
    else:
        median_ppsf = statistics.median(trimmed)
        mean_ppsf = sum(trimmed, Decimal("0")) / Decimal(len(trimmed))
        p25_ppsf = _quantile_decimal(trimmed, 0)
        p75_ppsf = _quantile_decimal(trimmed, 2)
        min_ppsf = trimmed[0]
        max_ppsf = trimmed[-1]

    confidence = float(min(1.0, math.log(n + 1) / math.log(101)))

    dated_dates = sorted(r.transaction_date for r in records if r.transaction_date)
    date_from = dated_dates[0] if dated_dates else None
    date_to = dated_dates[-1] if dated_dates else None

    yoy_available = False
    yoy_pct: Decimal | None = None
    yoy_p25_pct: Decimal | None = None
    if date_to is not None:
        yoy_available, yoy_pct = _yoy(records, date_to)
        _, yoy_p25_pct = _yoy_p25(records, date_to)

    cagr_3y_lower_pct = _cagr_3y_lower(records)
    conservative_growth_pct, growth_suggestion_available, growth_method = _conservative_growth(
        yoy_p25_pct, cagr_3y_lower_pct
    )

    emirate_out = matched_scope.emirate
    if emirate_out is None:
        emirates = [r.emirate for r in records if r.emirate]
        if emirates:
            emirate_out = Counter(emirates).most_common(1)[0][0]

    return AreaStatsResult(
        ok=True,
        area=matched_scope.area,
        emirate=emirate_out,
        matched_scope=matched_scope,
        transaction_count=n,
        median_ppsf=median_ppsf,
        mean_ppsf=mean_ppsf,
        p25_ppsf=p25_ppsf,
        p75_ppsf=p75_ppsf,
        min_ppsf=min_ppsf,
        max_ppsf=max_ppsf,
        confidence=confidence,
        date_from=date_from,
        date_to=date_to,
        yoy_available=yoy_available,
        yoy_pct=yoy_pct,
        message=None,
        yoy_p25_pct=yoy_p25_pct,
        cagr_3y_lower_pct=cagr_3y_lower_pct,
        conservative_growth_pct=conservative_growth_pct,
        growth_suggestion_available=growth_suggestion_available,
        growth_method=growth_method,
    )
