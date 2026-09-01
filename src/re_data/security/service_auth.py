from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from re_data.config import Settings, get_settings

# SEC-ENG-ST-053: reject oversized credentials before UTF-8 encode / compare_digest.
# 403 (not 401) uses the same stable shape as wrong tokens — no oracle on length alone.
MAX_BEARER_CREDENTIAL_LEN: int = 512

# auto_error=False lets us emit the locked error strings instead of FastAPI defaults.
_bearer_scheme = HTTPBearer(auto_error=False)

_INVALID_TOKEN_DETAIL = {
    "error": "Invalid service token",
    "details": ["Bearer token rejected"],
}


async def require_admin_bearer_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    settings: Settings = Depends(get_settings),
) -> None:
    """FastAPI dependency for /v1/admin/* routes.

    Execution order (REQ FR-2 → FR-3):
      1. Token not configured (empty/whitespace)  → 503 misconfiguration
      2. Header absent / non-Bearer scheme        → 401
      3. Bearer credential empty / whitespace     → 401
      4. Credential present, no match             → 403 (timing-safe compare)
      5. Match                                    → proceed (return None)

    Not attached to global middleware so /health, /v1/dataset, and /v1/market/*
    routes remain unauthenticated.
    """
    configured_token: str = (settings.data_service_token or "").strip()

    # ── Step 1: fail-closed when token not configured ────────────────────────
    if not configured_token:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "Admin reload auth not configured",
                "details": ["Set DATA_SERVICE_TOKEN in the service environment"],
            },
        )

    # ── Step 2: header absent or non-Bearer scheme ───────────────────────────
    # HTTPBearer(auto_error=False) returns None for both missing header AND
    # wrong scheme. Inspect the raw header to emit the correct error string.
    if credentials is None:
        raw_auth = request.headers.get("authorization", "")
        if raw_auth:
            # Header present — but scheme is not Bearer (e.g. Basic, Token)
            scheme, _, _ = raw_auth.partition(" ")
            if scheme.lower() != "bearer":
                raise HTTPException(
                    status_code=401,
                    detail={
                        "error": "Invalid authorization scheme",
                        "details": ["Use Authorization: Bearer <token>"],
                    },
                )
        # Header absent or bare "Bearer" with no credential
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Missing bearer token",
                "details": ["Authorization: Bearer <token> required"],
            },
        )

    # ── Step 3: Bearer present but credential empty ──────────────────────────
    credential: str = (credentials.credentials or "").strip()
    if not credential:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Missing bearer token",
                "details": ["Authorization: Bearer <token> required"],
            },
        )

    # ── Step 4: credential length cap (before encode / compare) ────────────
    if len(credential) > MAX_BEARER_CREDENTIAL_LEN:
        raise HTTPException(status_code=403, detail=_INVALID_TOKEN_DETAIL)

    # ── Step 5: timing-safe compare ──────────────────────────────────────────
    # Always call compare_digest regardless of length mismatch to prevent
    # timing side-channels. hmac.compare_digest requires equal-length bytes;
    # when lengths differ we pad/truncate the credential to the configured
    # token's length (padded credential always fails the compare — correctness
    # is preserved). Both branches run compare_digest to equalise CPU time.
    token_bytes = configured_token.encode("utf-8")
    cred_bytes = credential.encode("utf-8")
    if len(cred_bytes) != len(token_bytes):
        padded = cred_bytes.ljust(len(token_bytes), b"\x00")[: len(token_bytes)]
        hmac.compare_digest(padded, token_bytes)  # always False; neutralises timing signal
        raise HTTPException(status_code=403, detail=_INVALID_TOKEN_DETAIL)

    if not hmac.compare_digest(cred_bytes, token_bytes):
        raise HTTPException(status_code=403, detail=_INVALID_TOKEN_DETAIL)
