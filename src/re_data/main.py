from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from re_data.cache import create_market_cache
from re_data.config import Settings, get_settings
from re_data.middleware.request_id import RequestIdMiddleware
from re_data.routers import admin, dataset, health, market
from re_data.store.dataset_store import DatasetStore

load_dotenv()


def _fmt_validation_errors(exc: RequestValidationError) -> list[str]:
    msgs: list[str] = []
    for err in exc.errors():
        loc = [str(x) for x in err.get("loc", []) if x not in ("body",)]
        field = ".".join(loc) if loc else "input"
        msg = err.get("msg", "Invalid value")
        msgs.append(f"{field}: {msg}")
    return msgs


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    store = DatasetStore(settings, max_reload_s=settings.max_reload_s)
    store.initial_load()
    app.state.dataset_store = store
    app.state.settings = settings
    app.state.market_cache = create_market_cache(settings)
    yield
    app.state.market_cache.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Dubai/UAE Real Estate Data Service",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(RequestIdMiddleware)

    # Register BearerAuth security scheme into the generated OpenAPI schema.
    # The scheme is referenced via openapi_extra={"security": [...]} on the
    # admin router's reload operation.
    def _custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            routes=app.routes,
        )
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["BearerAuth"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "opaque",
            "description": (
                "DATA_SERVICE_TOKEN — operator-managed shared secret for /v1/admin/* routes"
            ),
        }
        app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = _custom_openapi  # type: ignore[method-assign]

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "Please check the highlighted inputs.",
                "details": _fmt_validation_errors(exc),
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, str):
            details = [exc.detail]
            error = exc.detail
        elif isinstance(exc.detail, list):
            details = [str(d) for d in exc.detail]
            error = details[0] if details else "Request failed"
        elif isinstance(exc.detail, dict):
            error = str(exc.detail.get("error", "Request failed"))
            raw_details = exc.detail.get("details", [])
            details = (
                [str(d) for d in raw_details]
                if isinstance(raw_details, list)
                else [str(raw_details)]
            )
        else:
            error = "Request failed"
            details = [str(exc.detail)]
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": error, "details": details},
        )

    @app.exception_handler(404)
    async def not_found_handler(_request: Request, _exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": "Not found", "details": ["The requested resource does not exist"]},
        )

    app.include_router(health.router)
    app.include_router(dataset.router)
    app.include_router(admin.router)
    app.include_router(market.router)

    @app.get("/")
    def root(settings: Settings = get_settings()) -> dict[str, Any]:
        return {
            "message": "Dubai/UAE Real Estate Data Service",
            "version": "0.1.0",
            "docs": "/docs",
            "health": "/health",
            "env": settings.app_env,
        }

    return app


app = create_app()
