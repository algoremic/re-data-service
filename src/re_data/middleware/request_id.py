"""ST-060b FINDING-09: request correlation id middleware.

Copied per-service (polyrepo). Keep in sync across core/data/ai/ml/user.

Client-supplied X-Request-ID is accepted only when non-empty after strip(),
<= REQUEST_ID_MAX_LEN chars, and matches REQUEST_ID_SAFE_RE. Invalid values
are replaced with a fresh UUID4 (never echoed).
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_MAX_LEN = 128
REQUEST_ID_SAFE_RE = re.compile(r"^[A-Za-z0-9._\-:+/=]+$")
_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

log = logging.getLogger(__name__)


def get_request_id() -> str | None:
    return _request_id_ctx.get()


def correlation_headers() -> dict[str, str]:
    rid = get_request_id()
    return {REQUEST_ID_HEADER: rid} if rid else {}


def _resolve_request_id(raw: str | None) -> str:
    if raw is not None:
        stripped = raw.strip()
        if (
            stripped
            and len(stripped) <= REQUEST_ID_MAX_LEN
            and REQUEST_ID_SAFE_RE.fullmatch(stripped)
        ):
            return stripped
    return str(uuid.uuid4())


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        token = _request_id_ctx.set(request_id)
        try:
            log.info(
                "request_id=%s method=%s path=%s",
                request_id,
                request.method,
                request.url.path,
            )
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            _request_id_ctx.reset(token)
