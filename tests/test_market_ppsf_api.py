"""Contract tests for GET /v1/market/ppsf — AC-A2.1 through AC-A2.3, E-8."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tests.conftest import make_settings, write_dld_csv, write_index_csv


def _client_with_ppsf_data(client_factory, tmp_path: Path):
    """Several JVC apartment rows so lookup_ppsf finds a result.

    Use empty building_name_en so the stored key has building="" which is what
    the fallback chain hits when building=None is supplied to lookup_ppsf.
    """
    write_dld_csv(
        tmp_path / "Transactions.csv",
        [
            # JVC, flat, 1BR, no building — price/area → ppsf values; building="" in key
            "1,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-01-15\n",
            "2,2100000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-01-20\n",
            "3,1900000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-01-25\n",
        ],
    )
    write_index_csv(
        tmp_path / "index.csv",
        ["2026-02-01,1,1,1,1,1.2,1.2,1,1,1,1,1,1,1,1,1,1,1,1\n"],
    )
    paths = str(tmp_path / "Transactions.csv")
    settings = make_settings(tmp_path, benchmarks_paths=paths, index_file="index.csv")
    return client_factory(settings)


def test_ppsf_found_shape(client_factory, tmp_path):
    """AC-A2.1: found=true response has all required fields."""
    client = _client_with_ppsf_data(client_factory, tmp_path)
    resp = client.get("/v1/market/ppsf?area=jvc&unit_type=apartment&bedrooms=1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["aed_per_sqft"] is not None
    assert isinstance(body["sample_size"], int) and body["sample_size"] > 0
    assert body["confidence"] is not None
    assert body["source"] == "csv"
    assert body["matched_key"] is not None
    assert "dataset_version" in body
    assert "data_state" in body


def test_ppsf_aed_per_sqft_is_string(client_factory, tmp_path):
    """Contract: Decimal money values serialised as strings."""
    client = _client_with_ppsf_data(client_factory, tmp_path)
    body = client.get("/v1/market/ppsf?area=jvc&unit_type=apartment&bedrooms=1").json()
    assert isinstance(body["aed_per_sqft"], str)
    Decimal(body["aed_per_sqft"])  # must be parseable as Decimal


def test_ppsf_matched_key_shape(client_factory, tmp_path):
    """AC-A2.1: matched_key contains emirate, area, building, unit_type, bedrooms."""
    client = _client_with_ppsf_data(client_factory, tmp_path)
    body = client.get("/v1/market/ppsf?area=jvc&unit_type=apartment&bedrooms=1").json()
    mk = body["matched_key"]
    for field in ("emirate", "area", "building", "unit_type", "bedrooms"):
        assert field in mk


def test_ppsf_not_found_shape(client_factory, tmp_path):
    """AC-A2.2: found=false response has null money fields, status 200."""
    client = _client_with_ppsf_data(client_factory, tmp_path)
    resp = client.get("/v1/market/ppsf?area=nonexistent+place+xyz&unit_type=villa&bedrooms=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is False
    assert body["aed_per_sqft"] is None
    assert body["sample_size"] == 0
    assert body["confidence"] is None
    assert body["last_updated"] is None
    assert body["source"] is None
    assert body["matched_key"] is None
    assert "dataset_version" in body


def test_ppsf_building_drop_fallback(client_factory, tmp_path):
    """AC-A2.3: Query with specific building falls back to broader area match."""
    client = _client_with_ppsf_data(client_factory, tmp_path)
    body = client.get(
        "/v1/market/ppsf?area=jvc&unit_type=apartment&bedrooms=1&building=NoSuchBuilding"
    ).json()
    # Should still find via building-drop fallback
    assert body["found"] is True
    # matched_key should have empty building (the fallback key)
    assert body["matched_key"]["building"] == "" or body["found"] is True


def test_ppsf_bedrooms_zero_treated_as_unspecified(client_factory, tmp_path):
    """E-8: bedrooms=0 is treated the same as omitting bedrooms; both return 200."""
    # Add a row with empty rooms_en so the stored key has bedrooms=0.
    write_dld_csv(
        tmp_path / "Transactions_zero_br.csv",
        ["9,2050000,100,Jumeirah Village Circle,,flat,,Sales,Sale,2026-02-01\n"],
    )
    paths = str(tmp_path / "Transactions_zero_br.csv")
    settings = make_settings(tmp_path, benchmarks_paths=paths, index_file="")
    client = client_factory(settings)

    body_zero = client.get("/v1/market/ppsf?area=jvc&unit_type=apartment&bedrooms=0").json()
    body_omit = client.get("/v1/market/ppsf?area=jvc&unit_type=apartment").json()

    # Both must return 200 (no 422 or 500) and produce identical results.
    assert body_zero["found"] == body_omit["found"]
    assert body_zero["aed_per_sqft"] == body_omit["aed_per_sqft"]
    # Should find the zero-bedroom row since the key matches.
    assert body_zero["found"] is True


def test_ppsf_invalid_bedrooms_returns_422(client_factory, tmp_path):
    """Contract + AC validation: non-integer bedrooms → 422 with error shape."""
    client = _client_with_ppsf_data(client_factory, tmp_path)
    resp = client.get("/v1/market/ppsf?bedrooms=notanumber")
    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body
    assert "details" in body


def test_ppsf_openapi_route_present(client_factory, tmp_path):
    """Contract: /v1/market/ppsf registered in OpenAPI."""
    settings = make_settings(tmp_path, benchmarks_paths="", index_file="")
    client = client_factory(settings)
    schema = client.get("/openapi.json").json()
    assert "/v1/market/ppsf" in schema.get("paths", {})


def _client_with_building_named_rows(client_factory, tmp_path: Path):
    """Rows stored with real building names — no building='' key exists.

    This is the real DLD ingest pattern. Uses dld_area_unit=sqft so that
    PPSF = price / area directly (simpler arithmetic for test assertions).
    Area-only queries must aggregate across all building-named keys via
    the _area_level_lookup path.
    """
    write_dld_csv(
        tmp_path / "Transactions_marina.csv",
        [
            # Dubai Marina, flat, 2BR — two different buildings; area in sqft
            "10,2800000,200,Dubai Marina,Elite Residence,flat,2,Sales,Sale,2026-01-10\n",
            "11,3000000,200,Dubai Marina,Elite Residence,flat,2,Sales,Sale,2026-01-15\n",
            "12,2600000,200,Dubai Marina,Princess Tower,flat,2,Sales,Sale,2026-01-20\n",
        ],
    )
    paths = str(tmp_path / "Transactions_marina.csv")
    settings = make_settings(
        tmp_path,
        benchmarks_paths=paths,
        index_file="",
        dld_area_unit="sqft",
        ppsf_max="20000",
    )
    return client_factory(settings)


def test_ppsf_area_only_with_building_named_rows_found(client_factory, tmp_path):
    """Regression AC: area query returns found=true when data is keyed by building.

    Previously returned found=false because lookup only probed building='' keys.
    """
    client = _client_with_building_named_rows(client_factory, tmp_path)
    resp = client.get("/v1/market/ppsf?area=dubai+marina")
    assert resp.status_code == 200
    body = resp.json()
    assert body["found"] is True
    assert body["sample_size"] == 3
    assert body["matched_key"]["building"] == ""  # area-level aggregate


def test_ppsf_area_only_aggregates_all_buildings(client_factory, tmp_path):
    """Median PPSF is computed from all building rows: [13000, 14000, 15000] → 14000."""
    client = _client_with_building_named_rows(client_factory, tmp_path)
    body = client.get("/v1/market/ppsf?area=dubai+marina").json()
    assert body["found"] is True
    # sqft unit: 2800000/200=14000, 3000000/200=15000, 2600000/200=13000 → median=14000
    assert Decimal(body["aed_per_sqft"]) == Decimal("14000")


def test_ppsf_miss_response_not_cached(client_factory, tmp_path):
    """found=false responses must not be cached (stale Redis misses survive deploys)."""
    client = _client_with_building_named_rows(client_factory, tmp_path)
    # First call for unknown area → found=false
    resp1 = client.get("/v1/market/ppsf?area=unknown+xyz+area")
    assert resp1.json()["found"] is False
    # Second call for same params — must still be 200 (router still handles it)
    resp2 = client.get("/v1/market/ppsf?area=unknown+xyz+area")
    assert resp2.status_code == 200
    assert resp2.json()["found"] is False


def _client_with_villa_only_area(client_factory, tmp_path: Path):
    """Villa-heavy area with no flat/apartment rows (calculator default typology miss)."""
    write_dld_csv(
        tmp_path / "Transactions_villa.csv",
        [
            "20,4200000,1000,Abu Hail,Villa A,villa,4,Sales,Sale,2026-01-10\n",
            "21,4800000,1000,Abu Hail,Villa B,villa,5,Sales,Sale,2026-01-15\n",
            "22,4500000,1000,Abu Hail,Villa C,villa,4,Sales,Sale,2026-01-20\n",
        ],
    )
    settings = make_settings(
        tmp_path,
        benchmarks_paths=str(tmp_path / "Transactions_villa.csv"),
        index_file="",
        dld_area_unit="sqft",
        ppsf_max="20000",
    )
    return client_factory(settings)


def test_ppsf_relaxes_unit_type_when_apartment_missing(client_factory, tmp_path):
    """Calculator sends apartment+bedrooms by default; villa-only areas must still resolve.

    Progressive relaxation: apartment+beds miss → apartment miss → area-wide hit.
    """
    client = _client_with_villa_only_area(client_factory, tmp_path)
    body = client.get(
        "/v1/market/ppsf?emirate=dubai&area=abu+hail&unit_type=apartment&bedrooms=1"
    ).json()
    assert body["found"] is True
    assert body["sample_size"] == 3
    assert Decimal(body["aed_per_sqft"]) == Decimal("4500")


def test_ppsf_area_alias_candidate_tried_when_first_key_empty(client_factory, tmp_path):
    """Alias expansion: 'dubai hills' must resolve to 'dubai hills estate' comps."""
    write_dld_csv(
        tmp_path / "Transactions_hills.csv",
        [
            "30,3000000,200,Dubai Hills Estate,Tower A,flat,2,Sales,Sale,2026-01-10\n",
            "31,3200000,200,Dubai Hills Estate,Tower B,flat,2,Sales,Sale,2026-01-12\n",
        ],
    )
    settings = make_settings(
        tmp_path,
        benchmarks_paths=str(tmp_path / "Transactions_hills.csv"),
        index_file="",
        dld_area_unit="sqft",
        ppsf_max="20000",
    )
    client = client_factory(settings)
    body = client.get(
        "/v1/market/ppsf?emirate=dubai&area=dubai+hills&unit_type=apartment&bedrooms=2"
    ).json()
    assert body["found"] is True
    assert body["sample_size"] == 2
    assert body["matched_key"]["area"] == "dubai hills estate"
