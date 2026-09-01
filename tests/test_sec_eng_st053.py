"""SEC-ENG-ST-053 break-tests and hardening regressions for admin reload auth."""

from __future__ import annotations

import pytest

from re_data.security.service_auth import MAX_BEARER_CREDENTIAL_LEN
from tests.conftest import auth_headers, make_settings, write_dld_csv


@pytest.fixture
def configured_client(client_factory, tmp_path):
    write_dld_csv(
        tmp_path / "Transactions.csv",
        ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"],
    )
    settings = make_settings(
        tmp_path,
        benchmarks_paths=str(tmp_path / "Transactions.csv"),
        index_file="",
        data_service_token="test-secret",
        admin_reload_cooldown_s=0.0,
    )
    return client_factory(settings)


class TestBearerLengthCap:
    """Oversized credentials must fail before encode/compare (403, stable error)."""

    def test_oversized_bearer_credential_403(self, configured_client) -> None:
        oversized = "x" * (MAX_BEARER_CREDENTIAL_LEN + 1)
        resp = configured_client.post("/v1/admin/reload", headers=auth_headers(oversized))
        assert resp.status_code == 403
        assert resp.json()["error"] == "Invalid service token"
        assert resp.json()["details"] == ["Bearer token rejected"]

    def test_max_length_boundary_still_compared(self, configured_client) -> None:
        """512-char wrong token is rejected via compare, not length cap."""
        at_max = "y" * MAX_BEARER_CREDENTIAL_LEN
        resp = configured_client.post("/v1/admin/reload", headers=auth_headers(at_max))
        assert resp.status_code == 403
        assert resp.json()["error"] == "Invalid service token"

    def test_oversized_bearer_does_not_reload(self, configured_client) -> None:
        pre_version = configured_client.get("/v1/dataset").json()["version"]
        oversized = "z" * (MAX_BEARER_CREDENTIAL_LEN + 64)
        resp = configured_client.post("/v1/admin/reload", headers=auth_headers(oversized))
        assert resp.status_code == 403
        assert configured_client.get("/v1/dataset").json()["version"] == pre_version


class TestEmptyTokenAntiRegression:
    """Empty configured or bearer credentials must never open admin reload."""

    def test_empty_configured_token_503_even_with_valid_bearer(
        self, client_factory, tmp_path
    ) -> None:
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
        client = client_factory(settings)
        for headers in (
            None,
            auth_headers("test-secret"),
            {"Authorization": "Bearer "},
            {"Authorization": "Bearer"},
        ):
            resp = client.post("/v1/admin/reload", headers=headers)
            assert resp.status_code == 503, headers
            assert resp.json()["error"] == "Admin reload auth not configured"

    def test_whitespace_only_configured_token_503(self, client_factory, tmp_path) -> None:
        write_dld_csv(
            tmp_path / "Transactions.csv",
            ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"],
        )
        settings = make_settings(
            tmp_path,
            benchmarks_paths=str(tmp_path / "Transactions.csv"),
            index_file="",
            data_service_token="   ",
        )
        client = client_factory(settings)
        resp = client.post("/v1/admin/reload", headers=auth_headers("anything"))
        assert resp.status_code == 503
        assert resp.json()["error"] == "Admin reload auth not configured"

    @pytest.mark.parametrize(
        "headers",
        [
            {},
            {"Authorization": "Bearer"},
            {"Authorization": "Bearer "},
            {"Authorization": "Bearer    "},
            {"Authorization": "Bearer \t\n"},
        ],
    )
    def test_empty_bearer_never_200(self, configured_client, headers: dict[str, str]) -> None:
        resp = configured_client.post("/v1/admin/reload", headers=headers)
        assert resp.status_code == 401
        assert resp.json()["error"] == "Missing bearer token"
        assert resp.status_code != 200


class TestAuthorizationHeaderFuzz:
    """Hostile Authorization header shapes must not bypass auth."""

    def test_null_byte_in_credential_rejected(self, configured_client) -> None:
        resp = configured_client.post(
            "/v1/admin/reload",
            headers={"Authorization": "Bearer test-secret\x00padding"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "Invalid service token"

    def test_unicode_homoglyph_token_rejected(self, configured_client) -> None:
        # Latin 'e' replaced with Cyrillic small letter ie (U+0435)
        homoglyph = "t\u0435st-secret"
        resp = configured_client.post(
            "/v1/admin/reload",
            headers=[(b"Authorization", f"Bearer {homoglyph}".encode("utf-8"))],
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "Invalid service token"

    def test_digest_scheme_rejected(self, configured_client) -> None:
        resp = configured_client.post(
            "/v1/admin/reload",
            headers={"Authorization": "Digest username=test"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"] == "Invalid authorization scheme"

    def test_bearer_lowercase_scheme_accepted_by_http_bearer(self, configured_client) -> None:
        resp = configured_client.post(
            "/v1/admin/reload",
            headers={"Authorization": "bearer test-secret"},
        )
        assert resp.status_code == 200

    def test_duplicate_authorization_first_valid_second_wrong(self, configured_client) -> None:
        """Duplicate headers: HTTP merges; HTTPBearer must not grant access on wrong tail."""
        resp = configured_client.post(
            "/v1/admin/reload",
            headers=[
                ("Authorization", "Bearer wrong"),
                ("Authorization", "Bearer test-secret"),
            ],
        )
        assert resp.status_code in (403, 401, 200)
        if resp.status_code == 200:
            pytest.fail("duplicate Authorization must not bypass auth when first value is wrong")

    def test_duplicate_authorization_both_wrong(self, configured_client) -> None:
        resp = configured_client.post(
            "/v1/admin/reload",
            headers=[
                ("Authorization", "Bearer wrong-one"),
                ("Authorization", "Bearer wrong-two"),
            ],
        )
        assert resp.status_code == 403
        assert resp.json()["error"] == "Invalid service token"


class TestConfiguredTokenLengthValidation:
    def test_oversized_data_service_token_rejected_at_settings(self, tmp_path) -> None:
        write_dld_csv(
            tmp_path / "Transactions.csv",
            ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"],
        )
        with pytest.raises(ValueError, match="at most 512"):
            make_settings(
                tmp_path,
                benchmarks_paths=str(tmp_path / "Transactions.csv"),
                index_file="",
                data_service_token="t" * 513,
            )


class TestReloadCooldownAvail01:
    """Authenticated reload spam: in-process cooldown blocks rapid re-trigger."""

    @pytest.fixture
    def cooldown_client(self, client_factory, tmp_path):
        write_dld_csv(
            tmp_path / "Transactions.csv",
            ["1,2000000,100,Dubai Marina,Tower,flat,2,Sales,Sale,2026-01-01\n"],
        )
        settings = make_settings(
            tmp_path,
            benchmarks_paths=str(tmp_path / "Transactions.csv"),
            index_file="",
            data_service_token="test-secret",
            admin_reload_cooldown_s=30.0,
        )
        return client_factory(settings)

    def test_immediate_second_reload_429(self, cooldown_client) -> None:
        headers = auth_headers("test-secret")
        first = cooldown_client.post("/v1/admin/reload", headers=headers)
        assert first.status_code == 200

        second = cooldown_client.post("/v1/admin/reload", headers=headers)
        assert second.status_code == 429
        assert second.json()["error"] == "Reload cooldown active"
        assert second.json()["details"] == ["retry after the cooldown window expires"]
