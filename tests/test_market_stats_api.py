"""Contract tests for GET /v1/market/stats/{area} — AC-3.1 through AC-3.6."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tests.conftest import make_settings, write_dld_csv


def _client_with_many_jvc_rows(client_factory, tmp_path: Path, n: int = 15):
    rows = [
        f"{i},{2000000 + i * 5000},100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-03-{(i % 28) + 1:02d}\n"
        for i in range(1, n + 1)
    ]
    write_dld_csv(tmp_path / "Transactions.csv", rows)
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    return client_factory(settings)


# ---------------------------------------------------------------------------
# AC-3.1 — happy path shape
# ---------------------------------------------------------------------------


def test_stats_happy_path_shape(client_factory, tmp_path):
    client = _client_with_many_jvc_rows(client_factory, tmp_path, n=15)
    resp = client.get("/v1/market/stats/jvc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["transaction_count"] >= 1
    assert body["median_ppsf"] is not None
    Decimal(body["median_ppsf"])
    assert body["p25_ppsf"] is not None
    assert body["p75_ppsf"] is not None
    assert 0.0 <= body["confidence"] <= 1.0
    assert body["source"] == "DLD public transaction data"


# ---------------------------------------------------------------------------
# AC-3.2 — unknown area -> ok:false, not 404
# ---------------------------------------------------------------------------


def test_stats_unknown_area_returns_ok_false(client_factory, tmp_path):
    client = _client_with_many_jvc_rows(client_factory, tmp_path, n=15)
    resp = client.get("/v1/market/stats/nonexistent_place")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["transaction_count"] == 0
    assert body["message"]


# ---------------------------------------------------------------------------
# AC-3.3 — unit_type filter narrows the computed set
# ---------------------------------------------------------------------------


def test_stats_unit_type_filter_narrows_matched_scope(client_factory, tmp_path):
    write_dld_csv(
        tmp_path / "Transactions.csv",
        [
            "1,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-03-15\n",
            "2,3500000,150,Jumeirah Village Circle,,villa,3,Sales,Sale,2024-03-16\n",
        ],
    )
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    client = client_factory(settings)
    resp = client.get("/v1/market/stats/jvc?unit_type=villa")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["matched_scope"]["unit_type"] == "villa"
    assert body["transaction_count"] == 1


# ---------------------------------------------------------------------------
# AC-3.4 — n < 10 uses full untrimmed sample, low confidence
# ---------------------------------------------------------------------------


def test_stats_small_sample_uses_full_sample_and_low_confidence(client_factory, tmp_path):
    write_dld_csv(
        tmp_path / "Transactions.csv",
        [
            "1,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-03-01\n",
            "2,2100000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-03-02\n",
            "3,1900000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-03-03\n",
        ],
    )
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    client = client_factory(settings)
    body = client.get("/v1/market/stats/jvc").json()
    assert body["transaction_count"] == 3
    # untrimmed median of 3 values
    assert body["min_ppsf"] is not None and body["max_ppsf"] is not None
    assert body["confidence"] < 0.52  # ~log(11)/log(101)


# ---------------------------------------------------------------------------
# AC-3.5 — YoY conditions not met -> yoy_available false
# ---------------------------------------------------------------------------


def test_stats_yoy_not_available_by_default(client_factory, tmp_path):
    client = _client_with_many_jvc_rows(client_factory, tmp_path, n=15)
    body = client.get("/v1/market/stats/jvc").json()
    assert body["yoy_available"] is False
    assert body["yoy_median_ppsf_change_pct"] is None


# ---------------------------------------------------------------------------
# YoY available: 10 current-window + 10 prior-window dated records
# ---------------------------------------------------------------------------


def test_stats_yoy_available_when_gate_conditions_met(client_factory, tmp_path):
    current_rows = [
        f"{i},2200000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-06-28\n"
        for i in range(1, 11)
    ]
    prior_rows = [
        f"{i},2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2023-01-01\n"
        for i in range(11, 21)
    ]
    write_dld_csv(tmp_path / "Transactions.csv", current_rows + prior_rows)
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    client = client_factory(settings)
    body = client.get("/v1/market/stats/jvc").json()

    assert body["yoy_available"] is True
    assert body["yoy_median_ppsf_change_pct"] is not None
    assert Decimal(body["yoy_median_ppsf_change_pct"]) > 0


# ---------------------------------------------------------------------------
# AC-3.6 — degraded snapshot
# ---------------------------------------------------------------------------


def test_stats_degraded_snapshot_returns_200(client_factory, tmp_path):
    settings = make_settings(tmp_path, benchmarks_paths="", index_file="")
    client = client_factory(settings)
    resp = client.get("/v1/market/stats/jvc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["data_state"] == "degraded"
    assert body["transaction_count"] == 0


# ---------------------------------------------------------------------------
# Edge E-3 — all transaction_date null -> date_range null, yoy_available false
# ---------------------------------------------------------------------------


def test_stats_all_null_dates_gives_null_date_range(client_factory, tmp_path):
    generic_csv = tmp_path / "generic.csv"
    generic_csv.write_text(
        "area,unit_type,bedrooms,price_aed,size_sqft\n"
        "Business Bay,studio,0,900000,500\n"
        "Business Bay,studio,0,950000,510\n",
        encoding="utf-8",
    )
    settings = make_settings(tmp_path, benchmarks_paths=str(generic_csv), index_file="")
    client = client_factory(settings)
    body = client.get("/v1/market/stats/business%20bay").json()
    assert body["ok"] is True
    assert body["date_range"]["from"] is None
    assert body["date_range"]["to"] is None
    assert body["yoy_available"] is False


# ---------------------------------------------------------------------------
# Edge E-11 — single-comp area
# ---------------------------------------------------------------------------


def test_stats_single_comp_area_percentiles_collapse(client_factory, tmp_path):
    write_dld_csv(
        tmp_path / "Transactions.csv",
        ["1,2000000,100,Remraam,,flat,1,Sales,Sale,2024-03-15\n"],
    )
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    client = client_factory(settings)
    body = client.get("/v1/market/stats/remraam").json()
    assert body["ok"] is True
    assert body["transaction_count"] == 1
    assert (
        body["median_ppsf"]
        == body["min_ppsf"]
        == body["max_ppsf"]
        == body["p25_ppsf"]
        == body["p75_ppsf"]
    )


# ---------------------------------------------------------------------------
# date_range JSON key is "from", not "from_"
# ---------------------------------------------------------------------------


def test_stats_date_range_json_key_is_from_not_from_(client_factory, tmp_path):
    client = _client_with_many_jvc_rows(client_factory, tmp_path, n=15)
    body = client.get("/v1/market/stats/jvc").json()
    assert "from" in body["date_range"]
    assert "from_" not in body["date_range"]


def test_stats_invalid_bedrooms_returns_422(client_factory, tmp_path):
    client = _client_with_many_jvc_rows(client_factory, tmp_path, n=15)
    resp = client.get("/v1/market/stats/jvc?bedrooms=two")
    assert resp.status_code == 422


def test_stats_openapi_route_present(client_factory, tmp_path):
    settings = make_settings(tmp_path, benchmarks_paths="", index_file="")
    client = client_factory(settings)
    schema = client.get("/openapi.json").json()
    assert "/v1/market/stats/{area}" in schema.get("paths", {})


# ---------------------------------------------------------------------------
# ST-027 DESIGN-ST-027-030 §4.1/§4.2 — worst-avg growth fields (additive)
# ---------------------------------------------------------------------------


def _ppsf_price(ppsf: int, size_sqm: int = 100) -> int:
    """AED price that yields exactly `ppsf` AED/sqft at `size_sqm` (DLD area unit)."""
    return round(ppsf * size_sqm * 10.7639104167)


def _client_with_growth_history(client_factory, tmp_path: Path):
    """Prior window (2023) p25=1000 vs median=1500; current window (2024) p25~1000

    vs median=2500 — bottom quartile barely moves while the median more than
    doubles, so p25 YoY (~0%) is far more pessimistic than median YoY (~67%).
    """
    prior_low = [
        f"{i},{_ppsf_price(1000)},100,Jumeirah Village Circle,,flat,1,Sales,Sale,2023-01-{i:02d}\n"
        for i in range(1, 6)
    ]
    prior_high = [
        f"{i},{_ppsf_price(2000)},100,Jumeirah Village Circle,,flat,1,Sales,Sale,2023-01-{i:02d}\n"
        for i in range(6, 11)
    ]
    current_low = [
        f"{i},{_ppsf_price(1000)},100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-06-{i:02d}\n"
        for i in range(20, 25)
    ]
    current_high = [
        f"{i},{_ppsf_price(4000)},100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-06-{i:02d}\n"
        for i in range(25, 30)
    ]
    write_dld_csv(
        tmp_path / "Transactions.csv", prior_low + prior_high + current_low + current_high
    )
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    return client_factory(settings)


def test_stats_growth_fields_present_and_p25_more_pessimistic_than_median(client_factory, tmp_path):
    """AC-1.2 / TASK-027-1: p25 YoY < median YoY -> conservative default uses p25."""
    client = _client_with_growth_history(client_factory, tmp_path)
    body = client.get("/v1/market/stats/jvc").json()

    assert body["yoy_available"] is True
    assert body["yoy_p25_ppsf_change_pct"] is not None
    assert body["growth_suggestion_available"] is True
    assert body["growth_method"] == "min(p25_yoy, cagr_3y_lower)"

    median_yoy = Decimal(body["yoy_median_ppsf_change_pct"])
    p25_yoy = Decimal(body["yoy_p25_ppsf_change_pct"])
    conservative = Decimal(body["conservative_growth_annual_pct"])

    assert p25_yoy < median_yoy
    assert conservative == p25_yoy  # only credible candidate (span < 24mo for CAGR)
    # Never derived from the (optimistic) median YoY (locked product rule).
    assert conservative != median_yoy


def test_stats_growth_never_uses_median_or_p75(client_factory, tmp_path):
    """Locked rule: conservative default must not equal p75/median PPSF-derived growth."""
    client = _client_with_growth_history(client_factory, tmp_path)
    body = client.get("/v1/market/stats/jvc").json()

    assert Decimal(body["conservative_growth_annual_pct"]) <= Decimal(
        body["yoy_median_ppsf_change_pct"]
    )


def test_stats_growth_unavailable_when_no_credible_history(client_factory, tmp_path):
    client = _client_with_many_jvc_rows(client_factory, tmp_path, n=15)
    body = client.get("/v1/market/stats/jvc").json()

    assert body["growth_suggestion_available"] is False
    assert body["conservative_growth_annual_pct"] is None
    assert body["growth_method"] is None
    # Existing behaviour unaffected.
    assert body["yoy_available"] is False


def test_stats_growth_unavailable_on_degraded_snapshot(client_factory, tmp_path):
    settings = make_settings(tmp_path, benchmarks_paths="", index_file="")
    client = client_factory(settings)
    body = client.get("/v1/market/stats/jvc").json()

    assert body["data_state"] == "degraded"
    assert body["growth_suggestion_available"] is False
    assert body["conservative_growth_annual_pct"] is None
    assert body["yoy_p25_ppsf_change_pct"] is None
    assert body["cagr_3y_lower_annual_pct"] is None


def test_stats_growth_fields_are_additive_backward_compatible(client_factory, tmp_path):
    """Existing AC-3.1 contract fields must be untouched by the ST-027 additions."""
    client = _client_with_many_jvc_rows(client_factory, tmp_path, n=15)
    body = client.get("/v1/market/stats/jvc").json()

    assert body["ok"] is True
    assert body["median_ppsf"] is not None
    assert body["p25_ppsf"] is not None
    assert body["p75_ppsf"] is not None
    assert body["source"] == "DLD public transaction data"
    # New fields exist without breaking old consumers that ignore unknown keys.
    for field in (
        "yoy_p25_ppsf_change_pct",
        "cagr_3y_lower_annual_pct",
        "conservative_growth_annual_pct",
        "growth_suggestion_available",
        "growth_method",
    ):
        assert field in body
