"""TASK-D contract tests for GET /v1/market/sales-activity (DESIGN-ST-075)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from re_data.market.comps import filter_and_sort_comps, filter_comps_unsorted
from re_data.market.sales_activity import MIN_SALES_ACTIVITY_N, compute_sales_activity
from re_data.models.domain import CompRecord, DatasetSnapshot, TotalsSummary
from tests.conftest import make_settings, write_dld_csv

# Inclusive window when area max is 2024-12-31 (2024 is a leap year):
#   start = end − (lookback_days − 1)
#   180 → 2024-07-05 .. 2024-12-31
#   365 → 2024-01-02 .. 2024-12-31
#   730 → 2023-01-02 .. 2024-12-31
_END = "2024-12-31"
_START = {
    180: (date.fromisoformat(_END) - timedelta(days=179)).isoformat(),
    365: (date.fromisoformat(_END) - timedelta(days=364)).isoformat(),
    730: (date.fromisoformat(_END) - timedelta(days=729)).isoformat(),
}


class _RecordingCache:
    def __init__(self) -> None:
        self.keys: list[tuple[str, str, dict[str, object]]] = []
        self.sets: list[tuple[str, BaseModel]] = []

    def build_key(self, endpoint: str, dataset_version: str, params: dict[str, object]) -> str:
        self.keys.append((endpoint, dataset_version, params))
        return f"{endpoint}:{dataset_version}:{params}"

    def get_model(self, key: str, model_type: type[Any]) -> Any | None:
        return None

    def set_model(self, key: str, value: BaseModel) -> None:
        self.sets.append((key, value))

    def close(self) -> None:
        return None


class _ScanDetectingMap(dict[str, tuple[CompRecord, ...]]):
    """Fails if a caller walks every area instead of `.get` on the matched key."""

    def values(self):  # type: ignore[override]
        raise AssertionError("city-wide scan via comp_records_by_area.values()")

    def items(self):  # type: ignore[override]
        raise AssertionError("city-wide scan via comp_records_by_area.items()")

    def __iter__(self):  # type: ignore[override]
        raise AssertionError("city-wide scan via iteration of comp_records_by_area")

    def keys(self):  # type: ignore[override]
        raise AssertionError("city-wide scan via comp_records_by_area.keys()")


def _comp(
    *,
    area: str,
    tx_date: str | None,
    unit_type: str = "flat",
    emirate: str = "dubai",
    tid: str = "1",
) -> CompRecord:
    return CompRecord(
        transaction_date=tx_date,
        price_aed=Decimal("2000000"),
        size_sqft=Decimal("100"),
        aed_per_sqft=Decimal("20000"),
        emirate=emirate,
        area=area,
        building="",
        unit_type=unit_type,
        bedrooms=1,
        transaction_id=tid,
    )


def _snapshot(
    by_area: dict[str, tuple[CompRecord, ...]],
    *,
    latest_transaction_date: str | None = _END,
    version: str = "test-version",
    state: str = "complete",
    record_count: int = 0,
) -> DatasetSnapshot:
    return DatasetSnapshot(
        version=version,
        ingested_at=datetime.now(UTC),
        state=state,  # type: ignore[arg-type]
        files=(),
        transactions={},
        last_dates={},
        index={},
        totals=TotalsSummary(
            accepted_transaction_rows=record_count,
            index_loaded=False,
            index_snapshot_count=0,
            latest_transaction_date=latest_transaction_date,
            comp_record_count=record_count,
        ),
        duplicate_content_hashes=(),
        comp_records_by_area=by_area,
    )


def _window_rows() -> list[str]:
    """JVC rows at inclusive boundaries plus a Marina decoy."""
    return [
        "1,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2022-12-31\n",
        "2,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2023-01-01\n",
        "3,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2023-01-02\n",
        "4,2000000,100,Jumeirah Village Circle,,villa,1,Sales,Sale,2024-01-01\n",
        "5,2000000,100,Jumeirah Village Circle,,villa,1,Sales,Sale,2024-01-02\n",
        "6,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-07-04\n",
        "7,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-07-05\n",
        "8,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-12-31\n",
        "9,2000000,100,Dubai Marina,,flat,1,Sales,Sale,2024-12-31\n",
    ]


def _client_with_rows(client_factory, tmp_path: Path, rows: list[str]):
    write_dld_csv(tmp_path / "Transactions.csv", rows)
    settings = make_settings(
        tmp_path, benchmarks_paths=str(tmp_path / "Transactions.csv"), index_file=""
    )
    return client_factory(settings)


def _assert_no_share_fields(body: dict[str, Any]) -> None:
    dumped = str(body).lower()
    assert "share_pct" not in dumped
    assert "share" not in dumped
    for slice_ in body.get("slices", []):
        assert set(slice_.keys()) == {"label", "count"}


def _assert_window_length(body: dict[str, Any], lookback_days: int) -> None:
    start = date.fromisoformat(body["date_range"]["from"])
    end = date.fromisoformat(body["date_range"]["to"])
    assert (end - start).days + 1 == lookback_days


# ---------------------------------------------------------------------------
# Happy 180 / 365 / 730 — inclusive window length + fixture counts
# ---------------------------------------------------------------------------


def test_sales_activity_happy_180_inclusive(client_factory, tmp_path):
    client = _client_with_rows(client_factory, tmp_path, _window_rows())
    resp = client.get("/v1/market/sales-activity?area=jvc&lookback_days=180")
    assert resp.status_code == 200
    body = resp.json()
    _assert_window_length(body, 180)
    assert body["date_range"]["from"] == _START[180]
    assert body["date_range"]["to"] == _END
    assert body["window_end_source"] == "area_max"
    assert body["transaction_count"] == 2
    assert body["slices"] == [{"label": "flat", "count": 2}]
    assert body["below_minimum_n"] is True
    assert body["source"] == "DLD public transaction data"
    assert body["matched_scope"]["area"] == "jumeirah village circle"
    _assert_no_share_fields(body)


def test_sales_activity_happy_365_inclusive(client_factory, tmp_path):
    client = _client_with_rows(client_factory, tmp_path, _window_rows())
    body = client.get("/v1/market/sales-activity?area=jvc&lookback_days=365").json()
    _assert_window_length(body, 365)
    assert body["date_range"]["from"] == _START[365]
    assert body["date_range"]["to"] == _END
    assert body["transaction_count"] == 4
    by_label = {s["label"]: s["count"] for s in body["slices"]}
    assert by_label == {"flat": 3, "villa": 1}


def test_sales_activity_happy_730_inclusive(client_factory, tmp_path):
    client = _client_with_rows(client_factory, tmp_path, _window_rows())
    body = client.get("/v1/market/sales-activity?area=jvc&lookback_days=730").json()
    _assert_window_length(body, 730)
    assert body["date_range"]["from"] == _START[730]
    assert body["date_range"]["to"] == _END
    # 2023-01-02 (start), 2024-01-01, 2024-01-02, 2024-07-04, 2024-07-05, 2024-12-31
    assert body["transaction_count"] == 6
    by_label = {s["label"]: s["count"] for s in body["slices"]}
    assert by_label == {"flat": 4, "villa": 2}


# ---------------------------------------------------------------------------
# Area-keyed only — helper must not scan every area
# ---------------------------------------------------------------------------


def test_filter_comps_unsorted_is_area_keyed_only():
    jvc = (_comp(area="jumeirah village circle", tx_date=_END, tid="jvc"),)
    marina = (_comp(area="dubai marina", tx_date=_END, tid="marina"),)
    by_area = _ScanDetectingMap(
        {
            "jumeirah village circle": jvc,
            "dubai marina": marina,
        }
    )
    snap = _snapshot(by_area)
    result = filter_comps_unsorted(
        snap,
        area="jvc",
        emirate="dubai",
        unit_type=None,
        bedrooms=0,
        building=None,
        relax=False,
    )
    assert result.total == 1
    assert result.records[0].transaction_id == "jvc"


def test_compute_sales_activity_is_area_keyed_only():
    jvc = tuple(
        _comp(area="jumeirah village circle", tx_date=_END, tid=str(i)) for i in range(3)
    )
    marina = tuple(_comp(area="dubai marina", tx_date=_END, tid=f"m{i}") for i in range(50))
    snap = _snapshot(
        _ScanDetectingMap(
            {
                "jumeirah village circle": jvc,
                "dubai marina": marina,
            }
        )
    )
    result = compute_sales_activity(snap, area="jvc", emirate="dubai", lookback_days=180)
    assert result.transaction_count == 3
    assert result.matched_area == "jumeirah village circle"


# ---------------------------------------------------------------------------
# Unknown area
# ---------------------------------------------------------------------------


def test_sales_activity_unknown_area_is_empty_200(client_factory, tmp_path):
    client = _client_with_rows(client_factory, tmp_path, _window_rows())
    resp = client.get("/v1/market/sales-activity?area=nonexistent_place&lookback_days=365")
    assert resp.status_code == 200
    body = resp.json()
    assert body["transaction_count"] == 0
    assert body["slices"] == []
    assert body["below_minimum_n"] is True
    assert body["window_end_source"] is None
    assert body["date_range"]["from"] is None
    assert body["date_range"]["to"] is None
    _assert_no_share_fields(body)


# ---------------------------------------------------------------------------
# Null dates excluded
# ---------------------------------------------------------------------------


def test_sales_activity_null_dates_excluded(client_factory, tmp_path):
    rows = [
        "1,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-12-31\n",
        "2,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,\n",
        "3,2000000,100,Jumeirah Village Circle,,villa,1,Sales,Sale,not-a-date\n",
    ]
    client = _client_with_rows(client_factory, tmp_path, rows)
    body = client.get("/v1/market/sales-activity?area=jvc&lookback_days=180").json()
    assert body["transaction_count"] == 1
    assert body["slices"] == [{"label": "flat", "count": 1}]


def test_compute_excludes_null_dates_from_count_and_end():
    records = (
        _comp(area="jumeirah village circle", tx_date=None, tid="undated"),
        _comp(area="jumeirah village circle", tx_date="2024-06-01", tid="dated"),
    )
    snap = _snapshot({"jumeirah village circle": records})
    result = compute_sales_activity(snap, area="jvc", emirate=None, lookback_days=180)
    assert result.transaction_count == 1
    assert result.date_to == "2024-06-01"
    assert result.window_end_source == "area_max"


# ---------------------------------------------------------------------------
# Empty unit_type → "unknown"
# ---------------------------------------------------------------------------


def test_sales_activity_empty_unit_type_is_unknown(client_factory, tmp_path):
    rows = [
        "1,2000000,100,Jumeirah Village Circle,,,1,Sales,Sale,2024-12-31\n",
        "2,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-12-31\n",
    ]
    client = _client_with_rows(client_factory, tmp_path, rows)
    body = client.get("/v1/market/sales-activity?area=jvc&lookback_days=180").json()
    assert body["transaction_count"] == 2
    by_label = {s["label"]: s["count"] for s in body["slices"]}
    assert by_label == {"flat": 1, "unknown": 1}


# ---------------------------------------------------------------------------
# lookback_days validation
# ---------------------------------------------------------------------------


def test_sales_activity_lookback_90_returns_422(client_factory, tmp_path):
    client = _client_with_rows(client_factory, tmp_path, _window_rows())
    resp = client.get("/v1/market/sales-activity?area=jvc&lookback_days=90")
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]
    assert isinstance(body["details"], list) and body["details"]
    assert any("lookback_days" in d for d in body["details"])


def test_sales_activity_area_over_256_returns_422(client_factory, tmp_path):
    """SE-075 / SA-075-02: defense-in-depth max_length on internal area query."""
    client = _client_with_rows(client_factory, tmp_path, _window_rows())
    resp = client.get(
        "/v1/market/sales-activity",
        params={"area": "x" * 257, "lookback_days": 365},
    )
    assert resp.status_code == 422


def test_sales_activity_path_like_area_is_empty_not_error(client_factory, tmp_path):
    """Break-test: path-like area is an unknown key, not filesystem access."""
    client = _client_with_rows(client_factory, tmp_path, _window_rows())
    resp = client.get(
        "/v1/market/sales-activity",
        params={"area": "../../etc/passwd", "lookback_days": 365},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["transaction_count"] == 0
    assert body["slices"] == []
    assert "share_pct" not in body


def test_sales_activity_lookback_36_returns_422(client_factory, tmp_path):
    client = _client_with_rows(client_factory, tmp_path, _window_rows())
    resp = client.get("/v1/market/sales-activity?area=jvc&lookback_days=36")
    assert resp.status_code == 422
    body = resp.json()
    assert "error" in body
    assert "details" in body
    assert any("lookback_days" in d for d in body["details"])


# ---------------------------------------------------------------------------
# Snapshot missing
# ---------------------------------------------------------------------------


def test_sales_activity_snapshot_missing_is_degraded(client_factory, tmp_path):
    settings = make_settings(tmp_path, benchmarks_paths="", index_file="")
    client = client_factory(settings)
    client.app.state.dataset_store._active = None
    resp = client.get("/v1/market/sales-activity?area=jvc&lookback_days=365")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data_state"] == "degraded"
    assert body["transaction_count"] == 0
    assert body["dataset_version"] == "none"
    assert body["slices"] == []
    assert body["below_minimum_n"] is True


# ---------------------------------------------------------------------------
# Cache key includes version + emirate + area + lookback_days
# ---------------------------------------------------------------------------


def test_sales_activity_cache_key_includes_version_emirate_area_lookback(
    client_factory, tmp_path
):
    client = _client_with_rows(client_factory, tmp_path, _window_rows())
    recorder = _RecordingCache()
    client.app.state.market_cache = recorder

    body = client.get(
        "/v1/market/sales-activity?area=jvc&emirate=Dubai&lookback_days=365"
    ).json()

    assert recorder.keys
    endpoint, version, params = recorder.keys[0]
    assert endpoint == "sales-activity"
    assert version == body["dataset_version"]
    assert version
    assert params["emirate"] == "dubai"
    assert params["area"] == "jvc"
    assert params["lookback_days"] == 365
    assert recorder.sets


# ---------------------------------------------------------------------------
# below_minimum_n at the N=10 gate
# ---------------------------------------------------------------------------


def test_below_minimum_n_true_when_count_9():
    records = tuple(
        _comp(area="jumeirah village circle", tx_date=_END, tid=str(i)) for i in range(9)
    )
    snap = _snapshot({"jumeirah village circle": records})
    result = compute_sales_activity(snap, area="jvc", emirate=None, lookback_days=180)
    assert result.transaction_count == 9
    assert result.below_minimum_n is True
    assert MIN_SALES_ACTIVITY_N == 10


def test_below_minimum_n_false_when_count_10():
    records = tuple(
        _comp(area="jumeirah village circle", tx_date=_END, tid=str(i)) for i in range(10)
    )
    snap = _snapshot({"jumeirah village circle": records})
    result = compute_sales_activity(snap, area="jvc", emirate=None, lookback_days=180)
    assert result.transaction_count == 10
    assert result.below_minimum_n is False


def test_sales_activity_below_minimum_n_api_gate(client_factory, tmp_path):
    nine = [
        f"{i},2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-12-31\n"
        for i in range(1, 10)
    ]
    client = _client_with_rows(client_factory, tmp_path, nine)
    body = client.get("/v1/market/sales-activity?area=jvc&lookback_days=180").json()
    assert body["transaction_count"] == 9
    assert body["below_minimum_n"] is True

    ten = nine + ["10,2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-12-31\n"]
    client10 = _client_with_rows(client_factory, tmp_path, ten)
    body10 = client10.get("/v1/market/sales-activity?area=jvc&lookback_days=180").json()
    assert body10["transaction_count"] == 10
    assert body10["below_minimum_n"] is False


# ---------------------------------------------------------------------------
# No share_pct on the wire
# ---------------------------------------------------------------------------


def test_sales_activity_response_has_no_share_pct(client_factory, tmp_path):
    client = _client_with_rows(client_factory, tmp_path, _window_rows())
    body = client.get("/v1/market/sales-activity?area=jvc&lookback_days=365").json()
    _assert_no_share_fields(body)
    assert "share_pct" not in body
    assert "remainder_pct" not in body


# ---------------------------------------------------------------------------
# Comps regression — extract did not drop pagination sort
# ---------------------------------------------------------------------------


def test_filter_and_sort_comps_still_applies_existing_sort_key():
    older = _comp(area="jumeirah village circle", tx_date="2024-01-01", tid="old")
    newer = _comp(area="jumeirah village circle", tx_date="2024-12-31", tid="new")
    # Input order is newest-first so a missing sort would fail the assertion below.
    snap = _snapshot({"jumeirah village circle": (newer, older)})

    unsorted = filter_comps_unsorted(
        snap,
        area="jvc",
        emirate=None,
        unit_type=None,
        bedrooms=0,
        building=None,
        relax=False,
    )
    assert [r.transaction_id for r in unsorted.records] == ["new", "old"]

    sorted_result = filter_and_sort_comps(
        snap,
        area="jvc",
        emirate=None,
        unit_type=None,
        bedrooms=0,
        building=None,
        relax=False,
    )
    assert [r.transaction_id for r in sorted_result.records] == ["old", "new"]
    assert [r.transaction_id for r in unsorted.records] == ["new", "old"]


def test_comps_pagination_still_works_after_helper_extract(client_factory, tmp_path):
    rows = [
        f"{i},2000000,100,Jumeirah Village Circle,,flat,1,Sales,Sale,2024-03-{(i % 28) + 1:02d}\n"
        for i in range(1, 41)
    ]
    client = _client_with_rows(client_factory, tmp_path, rows)
    page1 = client.get("/v1/market/comps?area=jvc&limit=20&skip=0").json()
    page2 = client.get("/v1/market/comps?area=jvc&limit=20&skip=20").json()
    assert page1["total"] == 40
    assert len(page1["items"]) == 20
    assert len(page2["items"]) == 20
    ids1 = {i["transaction_id"] for i in page1["items"]}
    ids2 = {i["transaction_id"] for i in page2["items"]}
    assert ids1.isdisjoint(ids2)
    dates1 = [i["transaction_date"] for i in page1["items"]]
    assert dates1 == sorted(dates1)


# ---------------------------------------------------------------------------
# Dataset-max fallback when matched rows exist but none are dated
# ---------------------------------------------------------------------------


def test_window_end_falls_back_to_dataset_max_when_area_undated():
    records = (_comp(area="jumeirah village circle", tx_date=None, tid="u1"),)
    snap = _snapshot(
        {"jumeirah village circle": records},
        latest_transaction_date="2024-12-31",
    )
    result = compute_sales_activity(snap, area="jvc", emirate=None, lookback_days=180)
    assert result.window_end_source == "dataset_max"
    assert result.date_to == "2024-12-31"
    assert result.date_from == (date(2024, 12, 31) - timedelta(days=179)).isoformat()
    assert result.transaction_count == 0
    assert result.below_minimum_n is True
