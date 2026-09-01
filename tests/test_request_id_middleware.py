"""ST-060b TASK-2: unit tests for RequestIdMiddleware in re-data-service.

Covers AC-1 (generate UUID when absent), AC-2 (echo when supplied),
AC-4 (middleware INFO log prefix).
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from re_data.middleware.request_id import (
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
    _request_id_ctx,
    _resolve_request_id,
    correlation_headers,
    get_request_id,
)


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------


def test_resolve_keeps_supplied_value():
    assert _resolve_request_id("my-correlation-id") == "my-correlation-id"


def test_resolve_strips_whitespace():
    assert _resolve_request_id("  trimmed  ") == "trimmed"


def test_resolve_generates_uuid_when_none():
    result = _resolve_request_id(None)
    parsed = uuid.UUID(result, version=4)
    assert str(parsed) == result


def test_resolve_generates_uuid_when_whitespace_only():
    result = _resolve_request_id("   ")
    parsed = uuid.UUID(result, version=4)
    assert str(parsed) == result


def test_resolve_rejects_oversize_value():
    attack = "a" * 129
    result = _resolve_request_id(attack)
    assert result != attack
    parsed = uuid.UUID(result, version=4)
    assert str(parsed) == result


def test_resolve_accepts_max_length_value():
    valid = "a" * 128
    assert _resolve_request_id(valid) == valid


def test_resolve_rejects_newline_in_value():
    attack = "valid-id\nX-Injected: evil"
    result = _resolve_request_id(attack)
    assert result != attack
    assert "\n" not in result
    parsed = uuid.UUID(result, version=4)
    assert str(parsed) == result


def test_resolve_rejects_crlf_in_value():
    attack = "evil\r\nSet-Cookie: bad"
    result = _resolve_request_id(attack)
    assert result != attack
    assert "\r" not in result
    assert "\n" not in result
    parsed = uuid.UUID(result, version=4)
    assert str(parsed) == result


def test_resolve_rejects_invalid_charset():
    attack = "bad$id"
    result = _resolve_request_id(attack)
    assert result != attack
    parsed = uuid.UUID(result, version=4)
    assert str(parsed) == result


def test_get_request_id_default_none():
    assert get_request_id() is None


def test_correlation_headers_empty_when_no_context():
    assert correlation_headers() == {}


def test_correlation_headers_populated_when_context_set():
    token = _request_id_ctx.set("data-test-id-abc")
    try:
        assert correlation_headers() == {REQUEST_ID_HEADER: "data-test-id-abc"}
    finally:
        _request_id_ctx.reset(token)


# ---------------------------------------------------------------------------
# Middleware integration via minimal ASGI app
# ---------------------------------------------------------------------------


@pytest.fixture
def middleware_app() -> TestClient:
    """Minimal FastAPI app with RequestIdMiddleware for middleware-level tests."""
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=True)


def test_middleware_generates_request_id_when_absent(middleware_app):
    """AC-1: response carries X-Request-ID even when client sends none."""
    resp = middleware_app.get("/ping")
    assert resp.status_code == 200
    rid = resp.headers.get(REQUEST_ID_HEADER)
    assert rid, "X-Request-ID header must be present"
    parsed = uuid.UUID(rid, version=4)
    assert str(parsed) == rid


def test_middleware_generates_distinct_ids_per_request(middleware_app):
    """AC-1: each request without a client-supplied id gets a unique id."""
    r1 = middleware_app.get("/ping")
    r2 = middleware_app.get("/ping")
    assert r1.headers[REQUEST_ID_HEADER] != r2.headers[REQUEST_ID_HEADER]


def test_middleware_echoes_supplied_request_id(middleware_app):
    """AC-2: when client sends X-Request-ID the response echoes it unchanged."""
    supplied = "test-correlation-123"
    resp = middleware_app.get("/ping", headers={REQUEST_ID_HEADER: supplied})
    assert resp.headers.get(REQUEST_ID_HEADER) == supplied


def test_middleware_echoes_arbitrary_opaque_string(middleware_app):
    """AC-2: arbitrary opaque string values are echoed verbatim."""
    supplied = "grep-me-060b"
    resp = middleware_app.get("/ping", headers={REQUEST_ID_HEADER: supplied})
    assert resp.headers.get(REQUEST_ID_HEADER) == supplied


def test_middleware_rejects_oversize_header(middleware_app):
    """SEC-F09-03: oversize X-Request-ID is replaced with fresh UUID4."""
    attack = "x" * 200
    resp = middleware_app.get("/ping", headers={REQUEST_ID_HEADER: attack})
    rid = resp.headers.get(REQUEST_ID_HEADER)
    assert rid != attack
    parsed = uuid.UUID(rid, version=4)
    assert str(parsed) == rid


def test_middleware_rejects_newline_header(middleware_app):
    """SEC-F09-03: newline in header value is replaced; response not split."""
    attack = "valid\nX-Injected: evil"
    resp = middleware_app.get("/ping", headers={REQUEST_ID_HEADER: attack})
    rid = resp.headers.get(REQUEST_ID_HEADER)
    assert rid != attack
    assert "\n" not in rid
    assert resp.headers.get("X-Injected") is None
    parsed = uuid.UUID(rid, version=4)
    assert str(parsed) == rid


def test_middleware_rejects_invalid_charset_header(middleware_app):
    """SEC-F09-03: invalid charset is replaced with fresh UUID4."""
    attack = "bad$id"
    resp = middleware_app.get("/ping", headers={REQUEST_ID_HEADER: attack})
    rid = resp.headers.get(REQUEST_ID_HEADER)
    assert rid != attack
    parsed = uuid.UUID(rid, version=4)
    assert str(parsed) == rid


def test_middleware_logs_request_info(middleware_app, caplog):
    """AC-4: middleware emits INFO log with request_id=, method=, path= fields."""
    import logging

    with caplog.at_level(logging.INFO, logger="re_data.middleware.request_id"):
        resp = middleware_app.get("/ping", headers={REQUEST_ID_HEADER: "log-test-id"})

    assert resp.status_code == 200
    assert any(
        "request_id=log-test-id" in r.message and "method=GET" in r.message and "path=/ping" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Full data service app smoke: /health echoes X-Request-ID (AC-1, AC-2)
# ---------------------------------------------------------------------------


def test_data_app_health_generates_request_id(client_factory, sample_complete_dataset):
    """AC-1: /health responds with X-Request-ID when none is sent."""
    client = client_factory(sample_complete_dataset)
    resp = client.get("/health")
    rid = resp.headers.get(REQUEST_ID_HEADER)
    assert rid, "X-Request-ID must be present on /health"
    uuid.UUID(rid, version=4)  # must be valid UUID4


def test_data_app_health_echoes_request_id(client_factory, sample_complete_dataset):
    """AC-2: /health echoes the supplied X-Request-ID."""
    client = client_factory(sample_complete_dataset)
    resp = client.get("/health", headers={REQUEST_ID_HEADER: "test-correlation-123"})
    assert resp.headers.get(REQUEST_ID_HEADER) == "test-correlation-123"
