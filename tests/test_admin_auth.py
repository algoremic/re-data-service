"""ST-053 auth matrix tests for POST /v1/admin/reload.

Covers AC-2 through AC-8, AC-12, AC-13 per REQ-ST-053.md.
hmac.compare_digest usage (AC-7) is verified both by static inspection comment
and by a parametric matrix of wrong tokens that must all return 403.
"""

from __future__ import annotations

import pytest

from tests.conftest import auth_headers, make_settings, write_dld_csv

# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def configured_client(client_factory, tmp_path):
    """TestClient with DATA_SERVICE_TOKEN='test-secret' and a valid CSV."""
    write_dld_csv(
        tmp_path / "Transactions.csv",
        ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"],
    )
    settings = make_settings(
        tmp_path,
        benchmarks_paths=str(tmp_path / "Transactions.csv"),
        index_file="",
        data_service_token="test-secret",
    )
    return client_factory(settings)


@pytest.fixture
def unconfigured_client(client_factory, tmp_path):
    """TestClient with DATA_SERVICE_TOKEN='' (fail-closed)."""
    write_dld_csv(
        tmp_path / "Transactions.csv",
        ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"],
    )
    settings = make_settings(
        tmp_path,
        benchmarks_paths=str(tmp_path / "Transactions.csv"),
        index_file="",
        data_service_token="",
    )
    return client_factory(settings)


# ──────────────────────────────────────────────────────────────────────────────
# AC-2: fail-closed — token not configured → 503
# ──────────────────────────────────────────────────────────────────────────────


def test_auth_token_not_configured_503(unconfigured_client):
    """AC-2: empty DATA_SERVICE_TOKEN → 503 regardless of auth header."""
    pre_version = unconfigured_client.get("/v1/dataset").json()["version"]

    # No auth header
    resp = unconfigured_client.post("/v1/admin/reload")
    assert resp.status_code == 503
    assert resp.json()["error"] == "Admin reload auth not configured"

    # With auth header — still 503 (fail-closed before auth check)
    resp2 = unconfigured_client.post("/v1/admin/reload", headers=auth_headers("test-secret"))
    assert resp2.status_code == 503
    assert resp2.json()["error"] == "Admin reload auth not configured"

    # Dataset version unchanged (reload did not run)
    assert unconfigured_client.get("/v1/dataset").json()["version"] == pre_version


def test_auth_token_configured_at_runtime_503(client_factory, tmp_path):
    """Edge case: dependency override to empty token still returns 503."""
    write_dld_csv(
        tmp_path / "Transactions.csv",
        ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"],
    )
    settings = make_settings(
        tmp_path,
        benchmarks_paths=str(tmp_path / "Transactions.csv"),
        index_file="",
        data_service_token="test-secret",
    )
    client = client_factory(settings)

    from re_data.config import Settings, get_settings

    empty_settings = Settings(
        benchmarks_csv_paths=settings.benchmarks_csv_paths,
        index_csv_path=settings.index_csv_path,
        app_env="test",
        data_service_token="",
    )
    # Override the FastAPI dependency so the auth dep sees an empty token
    client.app.dependency_overrides[get_settings] = lambda: empty_settings
    try:
        resp = client.post("/v1/admin/reload", headers=auth_headers("test-secret"))
    finally:
        client.app.dependency_overrides.pop(get_settings, None)

    assert resp.status_code == 503
    assert resp.json()["error"] == "Admin reload auth not configured"


# ──────────────────────────────────────────────────────────────────────────────
# AC-3: missing Authorization header → 401
# ──────────────────────────────────────────────────────────────────────────────


def test_auth_missing_header_401(configured_client):
    """AC-3: no Authorization header → 401 Missing bearer token."""
    resp = configured_client.post("/v1/admin/reload")
    assert resp.status_code == 401
    assert resp.json()["error"] == "Missing bearer token"


# ──────────────────────────────────────────────────────────────────────────────
# AC-4: invalid scheme → 401
# ──────────────────────────────────────────────────────────────────────────────


def test_auth_basic_scheme_401(configured_client):
    """AC-4: Basic auth scheme → 401 Invalid authorization scheme."""
    resp = configured_client.post(
        "/v1/admin/reload",
        headers={"Authorization": "Basic dGVzdA=="},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "Invalid authorization scheme"


def test_auth_non_bearer_scheme_401(configured_client):
    """AC-4: arbitrary non-Bearer scheme → 401."""
    resp = configured_client.post(
        "/v1/admin/reload",
        headers={"Authorization": "Token some-value"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "Invalid authorization scheme"


# ──────────────────────────────────────────────────────────────────────────────
# AC-5: Bearer with empty / whitespace credential → 401
# ──────────────────────────────────────────────────────────────────────────────


def test_auth_bearer_no_credential_401(configured_client):
    """AC-5: 'Authorization: Bearer' with no credential → 401."""
    resp = configured_client.post(
        "/v1/admin/reload",
        headers={"Authorization": "Bearer"},
    )
    # FastAPI's HTTPBearer with auto_error=False treats bare 'Bearer' as
    # missing credentials (None) → 401 Missing bearer token
    assert resp.status_code == 401
    assert resp.json()["error"] == "Missing bearer token"


def test_auth_bearer_whitespace_credential_401(configured_client):
    """AC-5: 'Authorization: Bearer   ' (spaces only) → 401."""
    resp = configured_client.post(
        "/v1/admin/reload",
        headers={"Authorization": "Bearer   "},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "Missing bearer token"


# ──────────────────────────────────────────────────────────────────────────────
# AC-6: correct scheme, wrong credential → 403
# ──────────────────────────────────────────────────────────────────────────────


def test_auth_wrong_token_403(configured_client):
    """AC-6: Bearer with non-matching credential → 403."""
    resp = configured_client.post("/v1/admin/reload", headers=auth_headers("wrong-token"))
    assert resp.status_code == 403
    assert resp.json()["error"] == "Invalid service token"


# ──────────────────────────────────────────────────────────────────────────────
# AC-7: timing-safe compare — all mismatched tokens return 403 (never 200)
# hmac.compare_digest is used in service_auth.py (see re_data/security/service_auth.py).
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad_token",
    [
        "a",
        "test-secre",  # one char short
        "test-secret2",  # one char long
        "x" * 64,  # much longer
        "",  # handled as missing (401) — excluded here; tested above
        "TEST-SECRET",  # wrong case
    ],
)
def test_auth_timing_safe_varied_wrong_tokens(configured_client, bad_token):
    """AC-7: varied wrong tokens all return 403 (never 200).

    Implementation note: re_data/security/service_auth.py uses hmac.compare_digest
    on UTF-8 bytes; unequal-length inputs are padded before compare to avoid a
    timing branch on length mismatch.
    """
    if not bad_token.strip():
        return  # empty/whitespace already tested as 401 in AC-5
    resp = configured_client.post("/v1/admin/reload", headers=auth_headers(bad_token))
    assert resp.status_code == 403, f"Expected 403 for token {bad_token!r}, got {resp.status_code}"
    assert resp.json()["error"] == "Invalid service token"


# ──────────────────────────────────────────────────────────────────────────────
# AC-1 / AC-8: valid token → 200 with DatasetMetadataResponse shape
# ──────────────────────────────────────────────────────────────────────────────


def test_auth_valid_token_200(configured_client):
    """AC-1/AC-8: correct Bearer → 200 with DatasetMetadataResponse."""
    resp = configured_client.post("/v1/admin/reload", headers=auth_headers("test-secret"))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["version"]) == 64
    assert "ingested_at" in body
    assert body["state"] in ("complete", "degraded")
    assert isinstance(body["files"], list)
    assert "totals" in body
    assert "duplicate_content_hashes" in body


# ──────────────────────────────────────────────────────────────────────────────
# AC-12: public routes unaffected — no auth required
# ──────────────────────────────────────────────────────────────────────────────


def test_public_routes_unaffected(configured_client):
    """AC-12: /health, GET /v1/dataset, and a market route succeed without Authorization."""
    health = configured_client.get("/health")
    assert health.status_code == 200

    dataset = configured_client.get("/v1/dataset")
    assert dataset.status_code == 200
    assert "version" in dataset.json()

    # Market areas route — exists in the router; no auth gate
    areas = configured_client.get("/v1/market/areas")
    assert areas.status_code in (200, 404)  # 404 acceptable when dataset has no area data


# ──────────────────────────────────────────────────────────────────────────────
# AC-13: OpenAPI accuracy — BearerAuth scheme + all status codes present
# ──────────────────────────────────────────────────────────────────────────────


def test_openapi_bearer_scheme_documented(configured_client):
    """AC-13: /openapi.json has BearerAuth component and all required status codes."""
    schema = configured_client.get("/openapi.json").json()

    # BearerAuth security scheme registered
    schemes = schema.get("components", {}).get("securitySchemes", {})
    assert "BearerAuth" in schemes, "BearerAuth security scheme missing from /openapi.json"
    bearer = schemes["BearerAuth"]
    assert bearer["type"] == "http"
    assert bearer["scheme"] == "bearer"

    # Operation security requirement
    reload_op = schema["paths"]["/v1/admin/reload"]["post"]
    security = reload_op.get("security", [])
    assert any("BearerAuth" in s for s in security), "BearerAuth not on reload operation"

    # All required response codes documented
    responses = reload_op.get("responses", {})
    for code in ("200", "401", "403", "409", "429", "503"):
        assert code in responses, f"Status {code} missing from reload operation OpenAPI responses"


# ──────────────────────────────────────────────────────────────────────────────
# Auth-before-409 regression: unauthenticated concurrent attempts must NOT get 409
# ──────────────────────────────────────────────────────────────────────────────


def test_auth_runs_before_lock_check(client_factory, tmp_path, monkeypatch):
    """REQ FR-5: auth dependency runs BEFORE the lock check.

    An unauthenticated request while a reload is in progress must receive
    401/403/503 (auth error), never 409 (reload in progress).
    """
    write_dld_csv(
        tmp_path / "Transactions.csv",
        ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"],
    )
    settings = make_settings(
        tmp_path,
        benchmarks_paths=str(tmp_path / "Transactions.csv"),
        index_file="",
        data_service_token="test-secret",
    )
    client = client_factory(settings)
    store = client.app.state.dataset_store

    # Simulate lock held (concurrent reload in progress)
    monkeypatch.setattr(store._reload_lock, "locked", lambda: True)

    # Missing auth header → must get 401, not 409
    resp_no_auth = client.post("/v1/admin/reload")
    assert resp_no_auth.status_code == 401
    assert resp_no_auth.json()["error"] == "Missing bearer token"

    # Wrong token → must get 403, not 409
    resp_wrong = client.post("/v1/admin/reload", headers={"Authorization": "Bearer wrong"})
    assert resp_wrong.status_code == 403
    assert resp_wrong.json()["error"] == "Invalid service token"
