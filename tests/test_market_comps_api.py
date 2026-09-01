"""Contract tests for GET /v1/market/comps — AC-2.1 through AC-2.7."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tests.conftest import make_settings, write_dld_csv


def _client_with_jvc_data(client_factory, tmp_path: Path):
    """A handful of JVC 1BR flat rows (one in a named building) + a villa row."""
    write_dld_csv(
        tmp_path / "Transactions.csv",
        [
            "1,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-03-15\n",
            "2,2100000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-03-20\n",
            "3,1900000,100,Jumeirah Village Circle,Binghatti Heights,flat,1,Sales,Sale,2024-03-25\n",
            "4,3500000,150,Jumeirah Village Circle,,villa,3,Sales,Sale,2024-02-01\n",
        ],
    )
    paths = str(tmp_path / "Transactions.csv")
    settings = make_settings(tmp_path, benchmarks_paths=paths, index_file="")
    return client_factory(settings)


def _client_with_many_jvc_comps(client_factory, tmp_path: Path, n: int = 50):
    rows = [
        f"{i},{2000000 + i * 1000},100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-03-{(i % 28) + 1:02d}\n"
        for i in range(1, n + 1)
    ]
    write_dld_csv(tmp_path / "Transactions.csv", rows)
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    return client_factory(settings)


# ---------------------------------------------------------------------------
# AC-2.1 — happy path shape
# ---------------------------------------------------------------------------


def test_comps_happy_path_shape(client_factory, tmp_path):
    client = _client_with_jvc_data(client_factory, tmp_path)
    resp = client.get("/v1/market/comps?area=jvc&unit_type=apartment&bedrooms=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert len(body["items"]) >= 1
    item = body["items"][0]
    assert isinstance(item["price_aed"], str)
    Decimal(item["price_aed"])
    assert isinstance(item["size_sqft"], str)
    Decimal(item["size_sqft"])
    assert isinstance(item["aed_per_sqft"], str)
    Decimal(item["aed_per_sqft"])
    assert body["source"] == "DLD public transaction data"
    assert isinstance(body["dataset_version"], str) and body["dataset_version"]
    assert body["matched_scope"]["area"] == "jumeirah village circle"
    assert body["data_state"] == "complete"


# ---------------------------------------------------------------------------
# AC-2.2 — missing area -> 422
# ---------------------------------------------------------------------------


def test_comps_missing_area_returns_422(client_factory, tmp_path):
    client = _client_with_jvc_data(client_factory, tmp_path)
    resp = client.get("/v1/market/comps")
    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body
    assert "details" in body


# ---------------------------------------------------------------------------
# AC-2.3 — zero matching comps -> 200, empty
# ---------------------------------------------------------------------------


def test_comps_zero_matches_returns_200_empty(client_factory, tmp_path):
    client = _client_with_jvc_data(client_factory, tmp_path)
    resp = client.get("/v1/market/comps?area=nonexistent_place")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []


# ---------------------------------------------------------------------------
# AC-2.4 — pagination
# ---------------------------------------------------------------------------


def test_comps_pagination_no_overlap(client_factory, tmp_path):
    client = _client_with_many_jvc_comps(client_factory, tmp_path, n=50)
    page1 = client.get("/v1/market/comps?area=jvc&limit=20&skip=0").json()
    page2 = client.get("/v1/market/comps?area=jvc&limit=20&skip=20").json()

    assert page1["total"] == 50
    assert page2["total"] == 50
    assert len(page1["items"]) == 20
    assert len(page2["items"]) == 20

    ids1 = {i["transaction_id"] for i in page1["items"]}
    ids2 = {i["transaction_id"] for i in page2["items"]}
    assert ids1.isdisjoint(ids2)


# ---------------------------------------------------------------------------
# AC-2.5 — limit=500 silently capped at 100
# ---------------------------------------------------------------------------


def test_comps_limit_silently_capped_at_100(client_factory, tmp_path):
    client = _client_with_many_jvc_comps(client_factory, tmp_path, n=50)
    resp = client.get("/v1/market/comps?area=jvc&limit=500")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) <= 100
    assert len(body["items"]) == 50  # only 50 exist


# ---------------------------------------------------------------------------
# AC-2.6 — relax=true widens strict-zero match
# ---------------------------------------------------------------------------


def test_comps_relax_widens_when_strict_empty(client_factory, tmp_path):
    client = _client_with_jvc_data(client_factory, tmp_path)
    strict = client.get("/v1/market/comps?area=jvc&unit_type=flat&bedrooms=9").json()
    assert strict["total"] == 0

    relaxed = client.get("/v1/market/comps?area=jvc&unit_type=flat&bedrooms=9&relax=true").json()
    assert relaxed["total"] >= 1
    assert relaxed["matched_scope"]["relaxed"] is True


# ---------------------------------------------------------------------------
# AC-2.7 — invalid bedrooms -> 422
# ---------------------------------------------------------------------------


def test_comps_invalid_bedrooms_returns_422(client_factory, tmp_path):
    client = _client_with_jvc_data(client_factory, tmp_path)
    resp = client.get("/v1/market/comps?area=jvc&bedrooms=two")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Degraded snapshot
# ---------------------------------------------------------------------------


def test_comps_degraded_snapshot_returns_200(client_factory, tmp_path):
    settings = make_settings(tmp_path, benchmarks_paths="", index_file="")
    client = client_factory(settings)
    resp = client.get("/v1/market/comps?area=jvc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_state"] == "degraded"
    assert body["total"] == 0
    assert body["items"] == []


# ---------------------------------------------------------------------------
# building exact-match filter + unknown unit_type passthrough
# ---------------------------------------------------------------------------


def test_comps_building_exact_match_filter(client_factory, tmp_path):
    client = _client_with_jvc_data(client_factory, tmp_path)
    body = client.get("/v1/market/comps?area=jvc&building=Binghatti+Heights&unit_type=flat").json()
    assert body["total"] == 1
    assert body["items"][0]["building"] == "binghatti heights"


def test_comps_unknown_unit_type_passes_through_without_error(client_factory, tmp_path):
    client = _client_with_jvc_data(client_factory, tmp_path)
    resp = client.get("/v1/market/comps?area=jvc&unit_type=duplex")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0


def test_comps_negative_skip_returns_422(client_factory, tmp_path):
    client = _client_with_jvc_data(client_factory, tmp_path)
    resp = client.get("/v1/market/comps?area=jvc&skip=-1")
    assert resp.status_code == 422


def test_comps_zero_limit_returns_422(client_factory, tmp_path):
    client = _client_with_jvc_data(client_factory, tmp_path)
    resp = client.get("/v1/market/comps?area=jvc&limit=0")
    assert resp.status_code == 422
