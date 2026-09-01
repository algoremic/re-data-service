"""Regression suite for area-level PPSF aggregation.

DLD ingest stores keys WITH real building/project names. lookup_ppsf must
aggregate all matching keys for an area when no building is requested, so MI
area-only queries return found=true from real transaction data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from re_data.ingest.parsers import key_tuple
from re_data.market.lookup import lookup_ppsf
from re_data.models.domain import CompRecord, DatasetSnapshot, TotalsSummary


def _snap(transactions: dict, last_dates: dict | None = None) -> DatasetSnapshot:
    """Build a minimal DatasetSnapshot for unit tests.

    Synthesises CompRecord objects from the transactions dict so that
    _area_level_lookup's area-indexed path (ADR-016) has the same data as the
    transactions dict.  All per-record dates are taken from last_dates[key] so
    that last_updated assertions are preserved.
    """
    ld = last_dates or {}
    by_area: dict[str, list[CompRecord]] = {}
    for key, values in transactions.items():
        emirate, area, building, unit_type, bedrooms = key
        key_date = ld.get(key)
        for ppsf in values:
            rec = CompRecord(
                transaction_date=key_date,
                price_aed=ppsf,  # dummy — only aed_per_sqft is tested
                size_sqft=Decimal("1"),  # dummy
                aed_per_sqft=ppsf,
                emirate=emirate,
                area=area,
                building=building,
                unit_type=unit_type,
                bedrooms=bedrooms,
                transaction_id=None,
            )
            by_area.setdefault(area, []).append(rec)

    return DatasetSnapshot(
        version="test",
        ingested_at=datetime.now(UTC),
        state="complete",
        files=(),
        transactions=transactions,
        last_dates=ld,
        index={},
        totals=TotalsSummary(
            accepted_transaction_rows=sum(len(v) for v in transactions.values()),
            index_loaded=False,
            index_snapshot_count=0,
            latest_transaction_date=None,
        ),
        duplicate_content_hashes=(),
        comp_records_by_area={k: tuple(v) for k, v in by_area.items()},
    )


def test_area_query_hits_when_only_building_level_keys_exist():
    """Regression: area query with building=None aggregates all building-keyed rows.

    Previously this returned None (found=false). After the fix, lookup_ppsf
    scans all keys matching emirate+area and returns a combined median.
    """
    tx = {
        key_tuple("dubai", "dubai marina", "elite residence", "flat", 2): (
            Decimal("1400"),
            Decimal("1500"),
        ),
        key_tuple("dubai", "dubai marina", "princess tower", "flat", 1): (Decimal("1600"),),
    }
    result = lookup_ppsf(
        _snap(tx),
        emirate="dubai",
        area="dubai marina",
        building=None,
        unit_type=None,
        bedrooms=0,
    )
    assert result is not None  # was None before fix — area rollup now hits
    assert result.stat.sample_size == 3
    assert result.matched_key[2] == ""  # building field is empty (area-level aggregate)


def test_area_query_median_is_correct():
    """Median of combined [1400, 1500, 1600] PPSF values = 1500."""
    tx = {
        key_tuple("dubai", "dubai marina", "elite residence", "flat", 2): (
            Decimal("1400"),
            Decimal("1500"),
        ),
        key_tuple("dubai", "dubai marina", "princess tower", "flat", 1): (Decimal("1600"),),
    }
    result = lookup_ppsf(
        _snap(tx),
        emirate="dubai",
        area="dubai marina",
        building=None,
        unit_type=None,
        bedrooms=0,
    )
    assert result is not None
    assert result.stat.aed_per_sqft == Decimal("1500")


def test_area_query_unit_type_filter_applied():
    """When unit_type is specified, only matching unit-type keys are aggregated."""
    tx = {
        key_tuple("dubai", "dubai marina", "elite residence", "flat", 2): (Decimal("1400"),),
        key_tuple("dubai", "dubai marina", "marina heights", "villa", 3): (Decimal("3000"),),
    }
    result = lookup_ppsf(
        _snap(tx),
        emirate="dubai",
        area="dubai marina",
        building=None,
        unit_type="apartment",  # alias for "flat"
        bedrooms=0,
    )
    assert result is not None
    assert result.stat.sample_size == 1
    assert result.stat.aed_per_sqft == Decimal("1400")


def test_area_query_bedrooms_filter_applied():
    """When bedrooms is specified, only matching bedroom-count keys contribute."""
    tx = {
        key_tuple("dubai", "dubai marina", "elite residence", "flat", 1): (Decimal("1800"),),
        key_tuple("dubai", "dubai marina", "princess tower", "flat", 2): (Decimal("1400"),),
    }
    result = lookup_ppsf(
        _snap(tx),
        emirate="dubai",
        area="dubai marina",
        building=None,
        unit_type=None,
        bedrooms=2,
    )
    assert result is not None
    assert result.stat.sample_size == 1
    assert result.stat.aed_per_sqft == Decimal("1400")


def test_area_query_returns_none_when_no_matching_keys():
    """When no keys match the area, lookup returns None."""
    tx = {
        key_tuple("dubai", "jvc", "some tower", "flat", 1): (Decimal("1200"),),
    }
    result = lookup_ppsf(
        _snap(tx),
        emirate="dubai",
        area="dubai marina",
        building=None,
        unit_type=None,
        bedrooms=0,
    )
    assert result is None


def test_area_query_last_updated_is_max_date():
    """last_updated reflects the most recent transaction date across all matched keys."""
    k1 = key_tuple("dubai", "dubai marina", "elite residence", "flat", 2)
    k2 = key_tuple("dubai", "dubai marina", "princess tower", "flat", 1)
    tx = {k1: (Decimal("1400"),), k2: (Decimal("1600"),)}
    last_dates = {k1: "2026-01-10", k2: "2026-03-15"}
    result = lookup_ppsf(
        _snap(tx, last_dates),
        emirate="dubai",
        area="dubai marina",
        building=None,
        unit_type=None,
        bedrooms=0,
    )
    assert result is not None
    assert result.stat.last_updated == "2026-03-15"


def test_building_query_still_uses_exact_key():
    """When building is provided, the exact-key chain takes priority."""
    tx = {
        key_tuple("dubai", "dubai marina", "elite residence", "flat", 2): (Decimal("1400"),),
        key_tuple("dubai", "dubai marina", "princess tower", "flat", 2): (Decimal("1600"),),
    }
    result = lookup_ppsf(
        _snap(tx),
        emirate="dubai",
        area="dubai marina",
        building="elite residence",
        unit_type="flat",
        bedrooms=2,
    )
    assert result is not None
    assert result.stat.aed_per_sqft == Decimal("1400")
    assert result.matched_key[2] == "elite residence"


def test_lookup_hits_when_empty_building_rollup_key_exists():
    """Pre-computed building='' aggregate keys are still matched (backward compat)."""
    tx = {
        key_tuple("dubai", "dubai marina", "elite residence", "flat", 2): (
            Decimal("1400"),
            Decimal("1500"),
        ),
        key_tuple("dubai", "dubai marina", "", "", 0): (
            Decimal("1400"),
            Decimal("1500"),
            Decimal("1600"),
        ),
    }
    result = lookup_ppsf(
        _snap(tx),
        emirate="dubai",
        area="dubai marina",
        building=None,
        unit_type=None,
        bedrooms=0,
    )
    assert result is not None
    # Area scan collects both keys → 5 values total (building row + empty rollup row)
    assert result.stat.sample_size == 5
    assert result.matched_key[2] == ""
