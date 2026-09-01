"""Tests for GET /v1/market/realized-path."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tests.conftest import make_settings, write_dld_csv


def _client_with_dated_comps(client_factory, tmp_path: Path):
    rows = [
        "1,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-01-15\n",
        "2,2100000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-04-15\n",
        "3,2200000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-07-15\n",
        "4,2300000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-10-15\n",
        "5,2400000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2025-01-15\n",
        "6,2500000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2025-04-15\n",
        "7,2600000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2025-07-15\n",
        "8,2700000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2025-10-15\n",
        "9,2800000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-01-15\n",
        "10,2900000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-04-15\n",
        "11,3000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2026-07-15\n",
    ]
    write_dld_csv(tmp_path / "Transactions.csv", rows)
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    return client_factory(settings)


def test_realized_path_happy_shape(client_factory, tmp_path):
    client = _client_with_dated_comps(client_factory, tmp_path)
    resp = client.get("/v1/market/realized-path?area=jvc&sale_date=2024-01-15")
    assert resp.status_code == 200
    body = resp.json()
    assert body["anchor_date"] == "2026-07-15"
    assert len(body["horizons"]) == 3
    assert body["horizons"][0]["horizon_months"] == 6
    assert "disclaimer" in body
    assert body["matched_scope"]["area"] == "jumeirah village circle"


def test_realized_path_immature_when_window_exceeds_anchor(client_factory, tmp_path):
    client = _client_with_dated_comps(client_factory, tmp_path)
    resp = client.get("/v1/market/realized-path?area=jvc&sale_date=2026-01-15")
    body = resp.json()
    h24 = next(h for h in body["horizons"] if h["horizon_months"] == 24)
    assert h24["status"] == "immature"
    assert h24["median_ppsf"] is None


def test_realized_path_below_minimum_n(client_factory, tmp_path):
    client = _client_with_dated_comps(client_factory, tmp_path)
    resp = client.get("/v1/market/realized-path?area=jvc&sale_date=2026-01-15")
    body = resp.json()
    h6 = next(h for h in body["horizons"] if h["horizon_months"] == 6)
    assert h6["status"] == "below_minimum_n"
    assert h6["sample_size"] == 2


def test_realized_path_ok_median(client_factory, tmp_path):
    from datetime import date, timedelta

    rows = []
    for i in range(12):
        d = date(2024, 2, 1) + timedelta(days=i * 14)
        rows.append(
            f"{i + 1},{2000000 + i * 1000},100,Jumeirah Village Circle,,flat,1,Sales,Sale,{d.isoformat()}\n"
        )
    for i in range(5):
        d = date(2025, 6, 1) + timedelta(days=i * 30)
        rows.append(
            f"{100 + i},{3000000 + i * 1000},100,Jumeirah Village Circle,,flat,1,Sales,Sale,{d.isoformat()}\n"
        )
    write_dld_csv(tmp_path / "Transactions.csv", rows)
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    client = client_factory(settings)
    resp = client.get("/v1/market/realized-path?area=jvc&sale_date=2024-01-15")
    h6 = next(h for h in resp.json()["horizons"] if h["horizon_months"] == 6)
    assert h6["status"] == "ok"
    assert Decimal(h6["median_ppsf"]) > 0
    assert h6["sample_size"] >= 10


def test_realized_path_invalid_sale_date_422(client_factory, tmp_path):
    client = _client_with_dated_comps(client_factory, tmp_path)
    resp = client.get("/v1/market/realized-path?area=jvc&sale_date=not-a-date")
    assert resp.status_code == 422
