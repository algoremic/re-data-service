from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from re_data.dependencies import StoreDep
from re_data.models.api import DatasetMetadataResponse, ErrorResponse
from re_data.routers.dataset import snapshot_to_response
from re_data.security.service_auth import require_admin_bearer_token
from re_data.store.dataset_store import (
    ReloadCooldownError,
    ReloadInProgressError,
    ReloadTimeoutError,
)

router = APIRouter(
    prefix="/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_bearer_token)],
)


@router.post(
    "/reload",
    response_model=DatasetMetadataResponse,
    responses={
        200: {"model": DatasetMetadataResponse},
        401: {"model": ErrorResponse, "description": "Missing or malformed bearer token"},
        403: {"model": ErrorResponse, "description": "Bearer token rejected"},
        409: {"model": ErrorResponse, "description": "Reload already in progress"},
        429: {"model": ErrorResponse, "description": "Reload cooldown active"},
        503: {
            "model": ErrorResponse,
            "description": (
                "Admin auth not configured (error: 'Admin reload auth not configured') "
                "or ingest timeout (error: 'Dataset reload timed out')"
            ),
        },
    },
    openapi_extra={"security": [{"BearerAuth": []}]},
)
async def reload_dataset(store: StoreDep) -> DatasetMetadataResponse | JSONResponse:
    try:
        snapshot = await store.reload()
    except ReloadInProgressError:
        return JSONResponse(
            status_code=409,
            content=ErrorResponse(
                error="Reload already in progress",
                details=["retry after the current ingest completes"],
            ).model_dump(),
        )
    except ReloadCooldownError:
        return JSONResponse(
            status_code=429,
            content=ErrorResponse(
                error="Reload cooldown active",
                details=["retry after the cooldown window expires"],
            ).model_dump(),
        )
    except ReloadTimeoutError:
        return JSONResponse(
            status_code=503,
            content=ErrorResponse(
                error="Dataset reload timed out",
                details=["Ingest exceeded MAX_RELOAD_S; prior snapshot unchanged"],
            ).model_dump(),
        )
    return snapshot_to_response(snapshot)
