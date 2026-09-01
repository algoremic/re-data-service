"""Unit tests for ST-027 worst-avg growth computation (DESIGN-ST-027-030 §4.1).

Exercises the pure calculation functions in `re_data.market.stats` directly
with hand-built `CompRecord`s so window/year boundaries are exact and the
tests do not depend on CSV ingestion or unit conversion rounding.
"""

from __future__ import annotations

from decimal import Decimal

from re_data.market.stats import (
    _cagr_3y_lower,
    _conservative_growth,
    _is_credible_rate,
    _yoy,
    _yoy_p25,
)
from re_data.models.domain import CompRecord

_ANCHOR = "2024-06-29"  # current window: [2023-07-01, 2024-06-29]; prior: [2022-07-01, 2023-06-30]


def _rec(ppsf: int, iso_date: str) -> CompRecord:
    return CompRecord(
        transaction_date=iso_date,
        price_aed=Decimal(ppsf) * Decimal(100),
        size_sqft=Decimal(100),
        aed_per_sqft=Decimal(ppsf),
        emirate="dubai",
        area="jumeirah village circle",
        building="",
        unit_type="flat",
        bedrooms=1,
        transaction_id=None,
    )


def _dated(day: int) -> str:
    return f"2024-06-{day:02d}"


def _prior_dated(day: int) -> str:
    return f"2023-01-{day:02d}"


# ---------------------------------------------------------------------------
# G1 — p25 YoY: AC-1.2 the more pessimistic (lower) eligible rate wins over
# a positive median YoY when the bottom quartile grew far less than the median.
# ---------------------------------------------------------------------------


def _g1_records() -> list[CompRecord]:
    # Prior window (2023-01-01..10): 5 @ 1000, 5 @ 2000 -> median 1500, p25 1000.
    prior = [_rec(1000, _prior_dated(d)) for d in range(1, 6)] + [
        _rec(2000, _prior_dated(d)) for d in range(6, 11)
    ]
    # Current window (2024-06-20..29): bottom half unchanged, top half tripled+
    # -> median 2500 (+66.7%), p25 stays ~1000 (~0%).
    current = [_rec(1000, _dated(d)) for d in range(20, 25)] + [
        _rec(4000, _dated(d)) for d in range(25, 30)
    ]
    return prior + current


def test_yoy_p25_lower_than_median_yoy():
    records = _g1_records()

    median_available, median_pct = _yoy(records, _ANCHOR)
    p25_available, p25_pct = _yoy_p25(records, _ANCHOR)

    assert median_available is True
    assert p25_available is True
    assert median_pct is not None and p25_pct is not None
    assert median_pct > Decimal("50")  # ~66.67%
    assert p25_pct < Decimal("5")  # ~0%
    assert p25_pct < median_pct  # AC-1.2: more pessimistic rate is the lower one


def test_yoy_p25_negative_when_market_contracts():
    # E-4: bottom quartile fell year-over-year -> negative rate is valid, not clamped to 0.
    prior = [_rec(2000, _prior_dated(d)) for d in range(1, 11)]
    current = [_rec(1800, _dated(d)) for d in range(20, 30)]

    available, pct = _yoy_p25(prior + current, _ANCHOR)

    assert available is True
    assert pct is not None
    assert pct < Decimal("0")


def test_yoy_p25_unavailable_below_window_gate():
    # Only 5 records/window -> below _MIN_WINDOW_RECORDS(10) -> same gate as median YoY.
    prior = [_rec(2000, _prior_dated(d)) for d in range(1, 6)]
    current = [_rec(2100, _dated(d)) for d in range(20, 25)]

    available, pct = _yoy_p25(prior + current, _ANCHOR)

    assert available is False
    assert pct is None


# ---------------------------------------------------------------------------
# G2 — 3y CAGR lower
# ---------------------------------------------------------------------------


def _year_records(year: int, ppsf: int, n: int = 10) -> list[CompRecord]:
    return [_rec(ppsf, f"{year}-03-{d:02d}") for d in range(1, n + 1)]


def test_cagr_3y_lower_uses_worse_of_cagr_and_yoy_min():
    records = _year_records(2021, 1000) + _year_records(2022, 1100) + _year_records(2023, 1200)

    result = _cagr_3y_lower(records)

    assert result is not None
    # CAGR(2021->2023) ~= 9.54%; worst adjacent YoY (2022->2023) ~= 9.09% -> pick lower.
    assert result == Decimal("9.09")


def test_cagr_3y_lower_none_when_span_under_24_months():
    # Only 2 consecutive credible years (12-month span) -> below _MIN_CAGR_SPAN_YEARS.
    records = _year_records(2022, 1000) + _year_records(2023, 1100)

    assert _cagr_3y_lower(records) is None


def test_cagr_3y_lower_none_when_only_one_credible_year():
    records = _year_records(2023, 1000)

    assert _cagr_3y_lower(records) is None


def test_cagr_3y_lower_none_when_credible_years_not_consecutive():
    # 2021 and 2023 are both credible but 2022 is missing -> no consecutive run >= 2.
    records = _year_records(2021, 1000) + _year_records(2023, 1300)

    assert _cagr_3y_lower(records) is None


def test_cagr_3y_lower_ignores_years_below_min_records():
    # 2022 only has 4 records (< _MIN_YEAR_RECORDS) -> not credible, breaks the run.
    records = _year_records(2021, 1000) + _year_records(2022, 1100, n=4) + _year_records(2023, 1200)

    assert _cagr_3y_lower(records) is None


def test_cagr_3y_lower_negative_when_prices_fell():
    # E-4: sustained decline across credible years is a valid (negative) result.
    records = _year_records(2021, 1200) + _year_records(2022, 1100) + _year_records(2023, 1000)

    result = _cagr_3y_lower(records)

    assert result is not None
    assert result < Decimal("0")


# ---------------------------------------------------------------------------
# Conservative selection + credibility clamp (§4.1, E-5)
# ---------------------------------------------------------------------------


def test_is_credible_rate_clamp_boundaries():
    assert _is_credible_rate(Decimal("50")) is True
    assert _is_credible_rate(Decimal("-50")) is True
    assert _is_credible_rate(Decimal("50.01")) is False
    assert _is_credible_rate(Decimal("-50.01")) is False
    assert _is_credible_rate(None) is False


def test_conservative_growth_selects_min_of_credible_candidates():
    value, available, method = _conservative_growth(Decimal("2.0"), Decimal("1.5"))

    assert available is True
    assert value == Decimal("1.5")
    assert method == "min(p25_yoy, cagr_3y_lower)"


def test_conservative_growth_never_uses_optimistic_candidate():
    # Even when p25 YoY is strongly positive, the pessimistic CAGR candidate wins.
    value, available, _ = _conservative_growth(Decimal("12.0"), Decimal("-3.0"))

    assert available is True
    assert value == Decimal("-3.0")


def test_conservative_growth_excludes_extreme_candidate_e5():
    # yoy_p25 exceeds the +-50% clamp -> not credible; cagr unavailable -> no suggestion.
    value, available, method = _conservative_growth(Decimal("75.0"), None)

    assert available is False
    assert value is None
    assert method is None


def test_conservative_growth_falls_back_to_single_credible_candidate():
    value, available, method = _conservative_growth(None, Decimal("-2.0"))

    assert available is True
    assert value == Decimal("-2.0")
    assert method == "min(p25_yoy, cagr_3y_lower)"


def test_conservative_growth_unavailable_when_no_candidates():
    value, available, method = _conservative_growth(None, None)

    assert available is False
    assert value is None
    assert method is None
