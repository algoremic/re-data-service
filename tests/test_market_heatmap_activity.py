"""GET /v1/market/heatmap-activity — city-wide windowed counts + median PPSF."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from re_data.market.heatmap_activity import compute_heatmap_activity
from re_data.models.domain import CompRecord, DatasetSnapshot, TotalsSummary


def _comp(area: str, tx_date: str | None, ppsf: str, *, emirate: str = "dubai") -> CompRecord:
    return CompRecord(
        transaction_date=tx_date,
        price_aed=Decimal("2000000"),
        size_sqft=Decimal("100"),
        aed_per_sqft=Decimal(ppsf),
        emirate=emirate,
        area=area,
        building="",
        unit_type="flat",
        bedrooms=1,
        transaction_id="1",
    )


def _snap(by_area: dict[str, tuple[CompRecord, ...]], latest: str | None = "2024-12-31") -> DatasetSnapshot:
    return DatasetSnapshot(
        version="v-hm",
        ingested_at=datetime.now(UTC),
        state="complete",
        files=(),
        transactions={},
        last_dates={},
        index={},
        totals=TotalsSummary(
            accepted_transaction_rows=1,
            index_loaded=False,
            index_snapshot_count=0,
            latest_transaction_date=latest,
            comp_record_count=1,
        ),
        duplicate_content_hashes=(),
        comp_records_by_area=by_area,
    )


def test_shared_dataset_window_and_median() -> None:
    snap = _snap(
        {
            "jumeirah village circle": (
                _comp("jumeirah village circle", "2024-12-31", "1000"),
                _comp("jumeirah village circle", "2024-12-31", "2000"),
                _comp("jumeirah village circle", "2023-01-01", "9000"),
            ),
            "dubai marina": (_comp("dubai marina", "2024-12-30", "3000"),),
        }
    )
    result = compute_heatmap_activity(snap, emirate="Dubai", lookback_days=180)
    assert result.date_to == "2024-12-31"
    assert result.date_from == "2024-07-05"
    by_area = {row.area: row for row in result.items}
    jvc = by_area["jvc"]
    assert jvc.transaction_count == 2
    assert jvc.median_ppsf == Decimal("1500")
    marina = by_area["dubai marina"]
    assert marina.transaction_count == 1
    assert marina.median_ppsf == Decimal("3000")


def test_null_dates_excluded_and_empty_window() -> None:
    snap = _snap(
        {
            "dubai marina": (
                _comp("dubai marina", None, "1000"),
                _comp("dubai marina", "2020-01-01", "1000"),
            )
        }
    )
    result = compute_heatmap_activity(snap, emirate="dubai", lookback_days=180)
    marina = next(row for row in result.items if row.area == "dubai marina")
    assert marina.transaction_count == 0
    assert marina.median_ppsf is None


def test_no_latest_date_returns_empty() -> None:
    result = compute_heatmap_activity(
        _snap({"dubai marina": (_comp("dubai marina", "2024-12-31", "1"),)}, latest=None),
        emirate="dubai",
        lookback_days=365,
    )
    assert result.items == []
    assert result.date_from is None


def test_lookback_days_422(client_factory, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import make_settings

    settings = make_settings(tmp_path)
    monkeypatch.setattr("re_data.dependencies.get_settings", lambda: settings)
    client = client_factory(settings)
    resp = client.get("/v1/market/heatmap-activity", params={"lookback_days": 90, "emirate": "Dubai"})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "Please check the highlighted inputs."
    assert any("lookback_days" in d for d in body["details"])


def test_no_share_pct_or_dataset_version_on_wire(client_factory, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from tests.conftest import make_settings, write_dld_csv

    write_dld_csv(
        tmp_path / "Transactions.csv",
        ["1,2000000,100,Dubai Marina,,flat,1,Sales,Sale,2024-12-31\n"],
    )
    settings = make_settings(tmp_path)
    monkeypatch.setattr("re_data.dependencies.get_settings", lambda: settings)
    client = client_factory(settings)
    resp = client.get("/v1/market/heatmap-activity", params={"lookback_days": 365, "emirate": "Dubai"})
    assert resp.status_code == 200
    body = resp.json()
    assert "dataset_version" not in body
    assert body["source"] == "DLD public transaction data"
    assert all("share_pct" not in item for item in body["items"])
