"""Parity tests — data-service market logic on known-good fixtures.

Covers AC-A2.4, AC-A3.4, AC-A1.5 (area value consistency on transaction subset).
All assertions are self-contained against static expected values in
tests/helpers/parity_reference.py — no dependency on realestate-core-service
benchmarks.py. Forward-compatible with ST-056 TASK-1 (benchmarks.py delete).
"""

from __future__ import annotations

import math
from decimal import Decimal
from pathlib import Path

from re_data.ingest.runner import run_ingest
from re_data.market.lookup import _calc_stat
from tests.conftest import make_settings, write_dld_csv, write_index_csv
from tests.helpers.parity_reference import (
    EXPECTED_CALC_STAT_N15_CONFIDENCE_APPROX,
    EXPECTED_CALC_STAT_N15_MEDIAN,
    EXPECTED_CALC_STAT_N15_N,
    EXPECTED_INDEX_DATE,
    EXPECTED_INDEX_VALUE,
    EXPECTED_JVC_1BR_PPSF_AED_SQFT,
    EXPECTED_PRICE_INDEX_VALUE,
    INDEX_ROWS,
    JVC_ROWS,
)

# ---------------------------------------------------------------------------
# Shared fixture helper
# ---------------------------------------------------------------------------


def _setup_fixture(tmp_path: Path):
    """Write shared CSVs and return DatasetSnapshot."""
    write_dld_csv(tmp_path / "Transactions.csv", JVC_ROWS)
    write_index_csv(tmp_path / "index.csv", INDEX_ROWS)
    paths = str(tmp_path / "Transactions.csv")
    settings = make_settings(tmp_path, benchmarks_paths=paths, index_file="index.csv")
    return run_ingest(settings)


# ---------------------------------------------------------------------------
# _calc_stat correctness
# ---------------------------------------------------------------------------


def test_calc_stat_trim_correctness_at_n15(tmp_path):
    """AC-A2.4: _calc_stat trim logic produces correct median and n on n=15 inputs."""
    vals = tuple(Decimal(str(100 + i * 10)) for i in range(15))
    result = _calc_stat(vals)
    assert result.median == EXPECTED_CALC_STAT_N15_MEDIAN, (
        f"median mismatch: {result.median} vs {EXPECTED_CALC_STAT_N15_MEDIAN}"
    )
    assert result.n == EXPECTED_CALC_STAT_N15_N
    assert math.isclose(result.confidence, EXPECTED_CALC_STAT_N15_CONFIDENCE_APPROX, rel_tol=1e-9)


def test_calc_stat_no_trim_below_10():
    """parity: _calc_stat with n<10 returns full-sample median."""
    vals = tuple(Decimal(str(x)) for x in [200, 300, 400])
    result = _calc_stat(vals)
    assert result.median == Decimal("300")
    assert result.n == 3


def test_calc_stat_single_value():
    """Edge case: single-value input."""
    vals = (Decimal("1500"),)
    result = _calc_stat(vals)
    assert result.median == Decimal("1500")
    assert result.n == 1
    assert result.confidence > 0


def test_calc_stat_empty():
    """Edge case: empty input returns zeros."""
    result = _calc_stat(())
    assert result.n == 0
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# PPSF expected values — AC-A2.4
# ---------------------------------------------------------------------------


def test_ppsf_jvc_apartment_1br_expected_value(tmp_path):
    """AC-A2.4: lookup_ppsf returns known-correct median for the JVC 1BR fixture."""
    from re_data.market.lookup import lookup_ppsf

    write_dld_csv(tmp_path / "Transactions.csv", JVC_ROWS)
    settings = make_settings(tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"))
    snapshot = run_ingest(settings)
    result = lookup_ppsf(
        snapshot,
        emirate="dubai",
        area="jvc",
        building=None,
        unit_type="apartment",
        bedrooms=1,
    )
    assert result is not None, "lookup_ppsf returned None for JVC 1BR fixture"
    assert result.stat.aed_per_sqft == EXPECTED_JVC_1BR_PPSF_AED_SQFT, (
        f"median mismatch: {result.stat.aed_per_sqft} vs {EXPECTED_JVC_1BR_PPSF_AED_SQFT}"
    )
    assert result.stat.sample_size == 4


def test_ppsf_nonexistent_area_returns_none(tmp_path):
    """AC-A2.2: lookup for completely absent area returns None."""
    from re_data.market.lookup import lookup_ppsf

    snapshot = _setup_fixture(tmp_path)

    result = lookup_ppsf(
        snapshot,
        emirate="dubai",
        area="totally nonexistent place xyz",
        building=None,
        unit_type="apartment",
        bedrooms=1,
    )
    assert result is None


def test_ppsf_building_drop_fallback(tmp_path):
    """AC-A2.3: Querying with non-existent building falls back to broader key.

    After the area-rollup fix the two paths take intentionally different routes:
    - building-specific query: exact-key chain → drops to building="" key
    - building=None query: area scan → returns area-level aggregate matched_key
    Both must hit and return non-None, but their matched_key shapes now differ.
    """
    from re_data.market.lookup import lookup_ppsf

    snapshot = _setup_fixture(tmp_path)

    result_specific = lookup_ppsf(
        snapshot,
        emirate="dubai",
        area="jvc",
        building="NoSuchBuilding",
        unit_type="apartment",
        bedrooms=1,
    )
    result_broad = lookup_ppsf(
        snapshot,
        emirate="dubai",
        area="jvc",
        building=None,
        unit_type="apartment",
        bedrooms=1,
    )
    assert result_specific is not None, "building-drop fallback should find a result"
    assert result_broad is not None, "area-only query should find a result via scan"
    # building-specific fallback resolves to building="" in the stored key
    assert result_specific.matched_key[2] == "", "specific-building path must drop to building=''"
    # area-only returns an area-level aggregate key (building="" signals area aggregate)
    assert result_broad.matched_key[2] == "", "area-only path must return building='' matched_key"


def test_ppsf_bedrooms_zero_same_as_none(tmp_path):
    """E-8: bedrooms=0 resolves identically to bedrooms=None."""
    from re_data.market.lookup import lookup_ppsf

    snapshot = _setup_fixture(tmp_path)

    res_zero = lookup_ppsf(
        snapshot,
        emirate="dubai",
        area="jvc",
        building=None,
        unit_type="apartment",
        bedrooms=0,
    )
    res_none = lookup_ppsf(
        snapshot,
        emirate="dubai",
        area="jvc",
        building=None,
        unit_type="apartment",
        bedrooms=None,
    )
    # key_tuple(bedrooms=0) == key_tuple(bedrooms=None) so results should be identical
    if res_zero is not None and res_none is not None:
        assert res_zero.matched_key == res_none.matched_key
    else:
        assert res_zero is None and res_none is None


# ---------------------------------------------------------------------------
# Index expected values — AC-A3.4
# ---------------------------------------------------------------------------


def test_index_expected_value(tmp_path):
    """AC-A3.4: latest_index returns known-correct date and values from the shared fixture."""
    from re_data.market.index import latest_index

    write_dld_csv(tmp_path / "Transactions.csv", JVC_ROWS)
    write_index_csv(tmp_path / "index.csv", INDEX_ROWS)
    settings = make_settings(
        tmp_path,
        benchmarks_paths=str(tmp_path / "Transactions.csv"),
        index_file="index.csv",
    )
    snapshot = run_ingest(settings)
    result = latest_index(snapshot, segment="all", horizon="yearly")
    assert result is not None
    assert result.date == EXPECTED_INDEX_DATE
    assert result.index_value == EXPECTED_INDEX_VALUE
    assert result.price_index_value == EXPECTED_PRICE_INDEX_VALUE


def test_index_absent_when_no_csv(tmp_path):
    """AC-A3.2: No index CSV → latest_index returns None."""
    from re_data.market.index import latest_index

    write_dld_csv(
        tmp_path / "Transactions.csv",
        ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-15\n"],
    )
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    snapshot = run_ingest(settings)
    result = latest_index(snapshot, segment="all", horizon="yearly")
    assert result is None


# ---------------------------------------------------------------------------
# Areas consistency — AC-A1.5
# ---------------------------------------------------------------------------


def test_areas_values_are_valid_alias_keys(tmp_path):
    """AC-A1.5: list_areas value keys are valid keys in the data-service alias map."""
    from re_data.market.aliases import _AREA_ALIASES
    from re_data.market.areas import list_areas

    write_dld_csv(tmp_path / "Transactions.csv", JVC_ROWS)
    settings = make_settings(tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"))
    snapshot = run_ingest(settings)
    ds_items, _ = list_areas(snapshot)
    ds_values = {i["value"] for i in ds_items}

    assert "jvc" in ds_values, "JVC (via fixture rows) should be in data-service areas"
    for value in ds_values:
        assert isinstance(value, str) and value, f"empty/non-string area value: {value!r}"
        assert value in _AREA_ALIASES or any(v == value for v in _AREA_ALIASES.values()), (
            f"area value {value!r} not in alias map"
        )
