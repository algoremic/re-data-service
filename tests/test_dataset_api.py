from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

from tests.conftest import (
    auth_headers,
    make_settings,
    write_dld_csv,
    write_index_csv,
    write_recent_csv,
)


def test_dataset_complete_metadata_shape(client_factory, tmp_path):
    write_dld_csv(
        tmp_path / "Transactions.csv",
        ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"],
    )
    write_recent_csv(
        tmp_path / "transactions-2026-02-23.csv",
        ["Sell,1500000,80,Marina,apartment,1\n"],
    )
    write_index_csv(
        tmp_path / "index.csv",
        ["2026-02-01,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1\n"],
    )
    paths = f"{tmp_path / 'transactions-2026-02-23.csv'};{tmp_path / 'Transactions.csv'}"
    settings = make_settings(tmp_path, benchmarks_paths=paths, index_file="index.csv")
    client = client_factory(settings)
    resp = client.get("/v1/dataset")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "complete"
    assert len(body["version"]) == 64
    assert "ingested_at" in body
    assert body["totals"]["accepted_transaction_rows"] == 2
    assert body["totals"]["index_loaded"] is True
    assert body["totals"]["latest_transaction_date"] == "2026-02-23"
    assert len(body["files"]) == 3


def test_duplicate_content_hashes_exposed(client_factory, tmp_path):
    content = "1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"
    p1 = tmp_path / "a.csv"
    p2 = tmp_path / "b.csv"
    write_dld_csv(p1, [content])
    write_dld_csv(p2, [content])
    settings = make_settings(tmp_path, benchmarks_paths=f"{p1};{p2}", index_file="")
    client = client_factory(settings)
    body = client.get("/v1/dataset").json()
    assert len(body["duplicate_content_hashes"]) == 1
    dup_file = next(f for f in body["files"] if f["duplicate_content_hash_of"])
    assert dup_file["rows_accepted"] == 0


def test_reload_e2e_version_change(client_factory, tmp_path):
    csv_path = tmp_path / "Transactions.csv"
    write_dld_csv(csv_path, ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"])
    settings = make_settings(tmp_path, benchmarks_paths=str(csv_path), index_file="")
    client = client_factory(settings)
    v1 = client.get("/v1/dataset").json()["version"]
    time.sleep(0.02)
    write_dld_csv(
        csv_path,
        [
            "1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n",
            "2,3000000,100,JVC,T,flat,1,Sales,Sale,2026-01-02\n",
        ],
    )
    reload = client.post("/v1/admin/reload", headers=auth_headers())
    assert reload.status_code == 200
    v2 = reload.json()["version"]
    assert v2 != v1
    assert client.get("/v1/dataset").json()["version"] == v2


def test_concurrent_reload_returns_409(client_factory, tmp_path, monkeypatch):
    csv_path = tmp_path / "Transactions.csv"
    write_dld_csv(csv_path, ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"])
    settings = make_settings(tmp_path, benchmarks_paths=str(csv_path), index_file="")
    client = client_factory(settings)
    store = client.app.state.dataset_store
    monkeypatch.setattr(store._reload_lock, "locked", lambda: True)
    resp = client.post("/v1/admin/reload", headers=auth_headers())
    assert resp.status_code == 409
    assert resp.json()["error"] == "Reload already in progress"


def test_openapi_v1_routes(client_factory, sample_complete_dataset):
    client = client_factory(sample_complete_dataset)
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/health" in paths
    assert "/v1/dataset" in paths
    assert "/v1/admin/reload" in paths
    reload_op = paths["/v1/admin/reload"]["post"]
    assert reload_op
    # BearerAuth security requirement must be present on the operation
    security = reload_op.get("security", [])
    assert any("BearerAuth" in s for s in security), "BearerAuth security not found on reload op"
    # All required status codes must be documented
    responses = reload_op.get("responses", {})
    for code in ("200", "401", "403", "409", "503"):
        assert code in responses, f"Status {code} not documented in OpenAPI"


def test_reload_timeout_returns_503_snapshot_preserved(client_factory, tmp_path):
    """AC-10/AC-11: timeout → 503 with prior snapshot unchanged; lock released for next reload."""
    csv_path = tmp_path / "Transactions.csv"
    write_dld_csv(csv_path, ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"])
    settings = make_settings(tmp_path, benchmarks_paths=str(csv_path), index_file="")
    client = client_factory(settings)
    pre_version = client.get("/v1/dataset").json()["version"]

    async def _hanging_ingest(*_args, **_kwargs) -> None:
        await asyncio.sleep(60)

    with patch("re_data.store.dataset_store.asyncio.to_thread", new=_hanging_ingest):
        # Override max_reload_s on the running store instance
        store = client.app.state.dataset_store
        original_max = store._max_reload_s
        store._max_reload_s = 0.05
        try:
            resp = client.post("/v1/admin/reload", headers=auth_headers())
        finally:
            store._max_reload_s = original_max

    assert resp.status_code == 503
    assert resp.json()["error"] == "Dataset reload timed out"
    # Prior snapshot unchanged
    assert client.get("/v1/dataset").json()["version"] == pre_version
    # Lock released — subsequent authorized reload succeeds (AC-11)
    assert not client.app.state.dataset_store.reload_in_progress
    resp2 = client.post("/v1/admin/reload", headers=auth_headers())
    assert resp2.status_code == 200
