"""Market read API — /v1/market/areas, /v1/market/ppsf, /v1/market/index/latest,
/v1/market/comps, /v1/market/stats/{area}, /v1/market/sales-activity.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from re_data.dependencies import MarketCacheDep, StoreDep
from re_data.market.aliases import _AREA_ALIASES, _norm
from re_data.market.areas import list_areas
from re_data.market.comps import filter_and_sort_comps
from re_data.market.heatmap_activity import compute_heatmap_activity
from re_data.market.index import latest_index
from re_data.market.lookup import lookup_ppsf
from re_data.market.realized_path import REALIZED_DISCLAIMER, compute_realized_path
from re_data.market.sales_activity import compute_sales_activity
from re_data.market.stats import compute_area_stats
from re_data.models.market_api import (
    AreaItem,
    AreasListResponse,
    AreaStatsResponse,
    CompItem,
    CompsMatchedScope,
    CompsResponse,
    DateRange,
    HeatmapActivityResponse,
    HeatmapAreaFact,
    IndexLatestResponse,
    MatchedKey,
    PpsfResponse,
    RealizedPathHorizon,
    RealizedPathResponse,
    SalesActivityMatchedScope,
    SalesActivityResponse,
)

router = APIRouter(prefix="/v1/market", tags=["market"])

_DEGRADED_VERSION = "none"
_SOURCE = "DLD public transaction data"
_ALLOWED_LOOKBACK_DAYS = frozenset({180, 365, 730})


@router.get("/areas", response_model=AreasListResponse)
def get_areas(
    emirate: str | None = Query(default=None, description="Filter by emirate (case-insensitive)"),
    store: StoreDep = ...,  # type: ignore[assignment]
    cache: MarketCacheDep = ...,  # type: ignore[assignment]
) -> AreasListResponse:
    """List all areas derived from the transaction dataset, with alias resolution."""
    snapshot = store.get_active()
    if snapshot is None:
        return AreasListResponse(
            items=[],
            dataset_version=_DEGRADED_VERSION,
            data_state="degraded",
        )

    emirate_norm = _norm(emirate) if emirate else None
    cache_key = cache.build_key(
        "areas",
        snapshot.version,
        {"emirate": emirate_norm or "all"},
    )
    cached = cache.get_model(cache_key, AreasListResponse)
    if cached is not None:
        return cached

    items, data_state = list_areas(snapshot, emirate=emirate_norm)

    response = AreasListResponse(
        items=[AreaItem(value=i["value"], label=i["label"], emirate=i["emirate"]) for i in items],
        dataset_version=snapshot.version,
        data_state=data_state,
    )
    cache.set_model(cache_key, response)
    return response


@router.get("/ppsf", response_model=PpsfResponse)
def get_ppsf(
    emirate: str | None = Query(default=None),
    area: str | None = Query(default=None),
    building: str | None = Query(default=None),
    unit_type: str | None = Query(default=None),
    bedrooms: int | None = Query(default=None, description="0 or omitted means unspecified"),
    store: StoreDep = ...,  # type: ignore[assignment]
    cache: MarketCacheDep = ...,  # type: ignore[assignment]
) -> PpsfResponse:
    """Look up AED/sqft benchmark with progressive-relaxation fallback."""
    snapshot = store.get_active()
    if snapshot is None:
        return PpsfResponse(
            found=False,
            dataset_version=_DEGRADED_VERSION,
            data_state="degraded",
        )

    bedrooms_norm = int(bedrooms or 0)
    cache_key = cache.build_key(
        "ppsf",
        snapshot.version,
        {
            "emirate": _norm(emirate) if emirate else "",
            "area": _norm(area) if area else "",
            "building": _norm(building) if building else "",
            "unit_type": _norm(unit_type) if unit_type else "",
            "bedrooms": bedrooms_norm,
        },
    )
    cached = cache.get_model(cache_key, PpsfResponse)
    if cached is not None:
        return cached

    result = lookup_ppsf(
        snapshot,
        emirate=emirate,
        area=area,
        building=building,
        unit_type=unit_type,
        bedrooms=bedrooms_norm,
    )

    if result is None:
        response = PpsfResponse(
            found=False,
            dataset_version=snapshot.version,
            data_state=snapshot.state,
        )
        # Do not cache miss responses — stale found=false entries would persist
        # across deploys that fix the underlying data or lookup logic.
        return response

    mk = result.matched_key
    response = PpsfResponse(
        found=True,
        aed_per_sqft=result.stat.aed_per_sqft,
        sample_size=result.stat.sample_size,
        confidence=result.stat.confidence,
        last_updated=result.stat.last_updated,
        source=result.stat.source,
        matched_key=MatchedKey(
            emirate=mk[0],
            area=mk[1],
            building=mk[2],
            unit_type=mk[3],
            bedrooms=mk[4],
        ),
        dataset_version=snapshot.version,
        data_state=snapshot.state,
    )
    cache.set_model(cache_key, response)
    return response


@router.get("/index/latest", response_model=IndexLatestResponse)
def get_index_latest(
    segment: Literal["all", "flat", "villa"] = Query(default="all"),
    horizon: Literal["monthly", "quarterly", "yearly"] = Query(default="yearly"),
    store: StoreDep = ...,  # type: ignore[assignment]
    cache: MarketCacheDep = ...,  # type: ignore[assignment]
) -> IndexLatestResponse:
    """Return the latest market index snapshot for the given segment and horizon."""
    snapshot = store.get_active()
    if snapshot is None:
        return IndexLatestResponse(
            ok=False,
            message="No index data loaded.",
            dataset_version=_DEGRADED_VERSION,
            data_state="degraded",
        )

    cache_key = cache.build_key(
        "index-latest",
        snapshot.version,
        {"segment": segment, "horizon": horizon},
    )
    cached = cache.get_model(cache_key, IndexLatestResponse)
    if cached is not None:
        return cached

    snap = latest_index(snapshot, segment=segment, horizon=horizon)

    if snap is None:
        response = IndexLatestResponse(
            ok=False,
            message="No index data loaded.",
            dataset_version=snapshot.version,
            data_state="degraded",
        )
        cache.set_model(cache_key, response)
        return response

    response = IndexLatestResponse(
        ok=True,
        date=snap.date,
        segment=snap.segment,
        horizon=snap.horizon,
        index_value=snap.index_value,
        price_index_value=snap.price_index_value,
        dataset_version=snapshot.version,
        data_state=snapshot.state,
    )
    cache.set_model(cache_key, response)
    return response


@router.get("/comps", response_model=CompsResponse)
def get_comps(
    area: str = Query(..., description="Area name or alias. Required."),
    emirate: str | None = Query(default=None),
    unit_type: str | None = Query(default=None),
    bedrooms: int = Query(default=0, ge=0),
    building: str | None = Query(default=None),
    relax: bool = Query(default=False),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1),
    store: StoreDep = ...,  # type: ignore[assignment]
    cache: MarketCacheDep = ...,  # type: ignore[assignment]
) -> CompsResponse:
    """Return paginated comparable sale transactions for an area (ADR-016 area-keyed lookup)."""
    actual_limit = min(limit, 100)  # AC-2.5: silent server-side cap

    snapshot = store.get_active()
    if snapshot is None:
        area_norm = _norm(area)
        area_canon = _AREA_ALIASES.get(area_norm, area_norm)
        return CompsResponse(
            total=0,
            items=[],
            matched_scope=CompsMatchedScope(area=area_canon, relaxed=False),
            source=_SOURCE,
            dataset_version=_DEGRADED_VERSION,
            data_state="degraded",
        )

    cache_key = cache.build_key(
        "comps",
        snapshot.version,
        {
            "area": _norm(area),
            "emirate": _norm(emirate) if emirate else "",
            "unit_type": _norm(unit_type) if unit_type else "",
            "bedrooms": bedrooms,
            "building": _norm(building) if building else "",
            "relax": relax,
            "skip": skip,
            "limit": actual_limit,
        },
    )
    cached = cache.get_model(cache_key, CompsResponse)
    if cached is not None:
        return cached

    result = filter_and_sort_comps(
        snapshot,
        area=area,
        emirate=emirate,
        unit_type=unit_type,
        bedrooms=bedrooms,
        building=building,
        relax=relax,
    )
    page = result.records[skip : skip + actual_limit]

    response = CompsResponse(
        total=result.total,
        items=[
            CompItem(
                transaction_date=r.transaction_date,
                price_aed=r.price_aed,
                size_sqft=r.size_sqft,
                aed_per_sqft=r.aed_per_sqft,
                emirate=r.emirate,
                area=r.area,
                building=r.building,
                unit_type=r.unit_type,
                bedrooms=r.bedrooms,
                transaction_id=r.transaction_id,
            )
            for r in page
        ],
        matched_scope=result.matched_scope,
        source=_SOURCE,
        dataset_version=snapshot.version,
        data_state=snapshot.state,
    )
    cache.set_model(cache_key, response)
    return response


@router.get("/realized-path", response_model=RealizedPathResponse)
def get_realized_path(
    area: str = Query(..., description="Area name or alias. Required."),
    sale_date: str = Query(..., description="Sale date (YYYY-MM-DD). Required."),
    emirate: str | None = Query(default=None),
    unit_type: str | None = Query(default=None),
    bedrooms: int = Query(default=0, ge=0),
    relax: bool = Query(default=False),
    store: StoreDep = ...,  # type: ignore[assignment]
    cache: MarketCacheDep = ...,  # type: ignore[assignment]
) -> RealizedPathResponse:
    """Area/segment median PPSF after sale_date for T+6/12/24 windows."""
    try:
        from datetime import date as _date

        _date.fromisoformat(sale_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Please check the highlighted inputs.",
                "details": [f"sale_date: {exc}"],
            },
        ) from exc

    snapshot = store.get_active()
    area_norm = _norm(area)
    area_canon = _AREA_ALIASES.get(area_norm, area_norm)
    if snapshot is None:
        return RealizedPathResponse(
            area=area_canon,
            sale_date=sale_date,
            anchor_date=None,
            horizons=[],
            disclaimer=REALIZED_DISCLAIMER,
            matched_scope=CompsMatchedScope(area=area_canon, relaxed=False),
            dataset_version=_DEGRADED_VERSION,
            data_state="degraded",
        )

    cache_key = cache.build_key(
        "realized-path",
        snapshot.version,
        {
            "area": area_norm,
            "sale_date": sale_date,
            "emirate": _norm(emirate) if emirate else "",
            "unit_type": _norm(unit_type) if unit_type else "",
            "bedrooms": bedrooms,
            "relax": relax,
        },
    )
    cached = cache.get_model(cache_key, RealizedPathResponse)
    if cached is not None:
        return cached

    result = compute_realized_path(
        snapshot,
        area=area,
        sale_date=sale_date,
        emirate=emirate,
        unit_type=unit_type,
        bedrooms=bedrooms,
        relax=relax,
    )
    response = RealizedPathResponse(
        area=result.area,
        sale_date=result.sale_date,
        anchor_date=result.anchor_date,
        horizons=[
            RealizedPathHorizon(
                horizon_months=h.horizon_months,  # type: ignore[arg-type]
                status=h.status,  # type: ignore[arg-type]
                median_ppsf=h.median_ppsf,
                sample_size=h.sample_size,
            )
            for h in result.horizons
        ],
        disclaimer=REALIZED_DISCLAIMER,
        matched_scope=result.matched_scope,
        dataset_version=result.dataset_version,
        data_state=result.data_state,  # type: ignore[arg-type]
    )
    cache.set_model(cache_key, response)
    return response


@router.get("/stats/{area}", response_model=AreaStatsResponse)
def get_area_stats(
    area: str,
    emirate: str | None = Query(default=None),
    unit_type: str | None = Query(default=None),
    bedrooms: int = Query(default=0, ge=0),
    relax: bool = Query(default=False),
    store: StoreDep = ...,  # type: ignore[assignment]
    cache: MarketCacheDep = ...,  # type: ignore[assignment]
) -> AreaStatsResponse:
    """Return distribution statistics for an area's comparable sale transactions."""
    snapshot = store.get_active()
    if snapshot is None:
        area_norm = _norm(area)
        area_canon = _AREA_ALIASES.get(area_norm, area_norm)
        return AreaStatsResponse(
            ok=False,
            area=area_canon,
            message="No transaction data for this area and filter combination.",
            transaction_count=0,
            source=_SOURCE,
            dataset_version=_DEGRADED_VERSION,
            data_state="degraded",
        )

    cache_key = cache.build_key(
        "stats",
        snapshot.version,
        {
            "area": _norm(area),
            "emirate": _norm(emirate) if emirate else "",
            "unit_type": _norm(unit_type) if unit_type else "",
            "bedrooms": bedrooms,
            "relax": relax,
        },
    )
    cached = cache.get_model(cache_key, AreaStatsResponse)
    if cached is not None:
        return cached

    result = compute_area_stats(
        snapshot,
        area=area,
        emirate=emirate,
        unit_type=unit_type,
        bedrooms=bedrooms,
        relax=relax,
    )

    response = AreaStatsResponse(
        ok=result.ok,
        area=result.area,
        emirate=result.emirate,
        matched_scope=result.matched_scope,
        transaction_count=result.transaction_count,
        median_ppsf=result.median_ppsf,
        mean_ppsf=result.mean_ppsf,
        p25_ppsf=result.p25_ppsf,
        p75_ppsf=result.p75_ppsf,
        min_ppsf=result.min_ppsf,
        max_ppsf=result.max_ppsf,
        confidence=result.confidence,
        date_range=DateRange.model_validate({"from": result.date_from, "to": result.date_to}),
        yoy_available=result.yoy_available,
        yoy_median_ppsf_change_pct=result.yoy_pct,
        message=result.message,
        source=_SOURCE,
        dataset_version=snapshot.version,
        data_state=snapshot.state,
        yoy_p25_ppsf_change_pct=result.yoy_p25_pct,
        cagr_3y_lower_annual_pct=result.cagr_3y_lower_pct,
        conservative_growth_annual_pct=result.conservative_growth_pct,
        growth_suggestion_available=result.growth_suggestion_available,
        growth_method=result.growth_method,
    )
    cache.set_model(cache_key, response)
    return response


@router.get("/sales-activity", response_model=SalesActivityResponse)
def get_sales_activity(
    area: str = Query(
        ...,
        max_length=256,
        description="Area name or alias. Required.",
    ),
    lookback_days: int = Query(
        ...,
        description="Inclusive lookback length in days. Must be 180, 365, or 730.",
    ),
    emirate: str | None = Query(default=None, max_length=64),
    store: StoreDep = ...,  # type: ignore[assignment]
    cache: MarketCacheDep = ...,  # type: ignore[assignment]
) -> SalesActivityResponse:
    """Return windowed sale counts by unit_type for one area (no pie shares)."""
    if lookback_days not in _ALLOWED_LOOKBACK_DAYS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Please check the highlighted inputs.",
                "details": ["lookback_days: Input should be 180, 365, or 730"],
            },
        )

    snapshot = store.get_active()
    if snapshot is None:
        area_norm = _norm(area)
        area_canon = _AREA_ALIASES.get(area_norm, area_norm)
        return SalesActivityResponse(
            transaction_count=0,
            date_range=DateRange(),
            window_end_source=None,
            slices=[],
            below_minimum_n=True,
            dataset_version=_DEGRADED_VERSION,
            data_state="degraded",
            source=_SOURCE,
            matched_scope=SalesActivityMatchedScope(
                emirate=_norm(emirate) if emirate else None,
                area=area_canon,
            ),
        )

    emirate_norm = _norm(emirate) if emirate else ""
    cache_key = cache.build_key(
        "sales-activity",
        snapshot.version,
        {
            "emirate": emirate_norm,
            "area": _norm(area),
            "lookback_days": lookback_days,
        },
    )
    cached = cache.get_model(cache_key, SalesActivityResponse)
    if cached is not None:
        return cached

    result = compute_sales_activity(
        snapshot,
        area=area,
        emirate=emirate,
        lookback_days=lookback_days,
    )
    response = SalesActivityResponse(
        transaction_count=result.transaction_count,
        date_range=DateRange.model_validate({"from": result.date_from, "to": result.date_to}),
        window_end_source=result.window_end_source,
        slices=result.slices,
        below_minimum_n=result.below_minimum_n,
        dataset_version=snapshot.version,
        data_state=snapshot.state,
        source=_SOURCE,
        matched_scope=SalesActivityMatchedScope(
            emirate=result.matched_emirate,
            area=result.matched_area,
        ),
    )
    cache.set_model(cache_key, response)
    return response


@router.get("/heatmap-activity", response_model=HeatmapActivityResponse)
def get_heatmap_activity(
    lookback_days: int = Query(
        ...,
        description="Inclusive lookback length in days. Must be 180, 365, or 730.",
    ),
    emirate: str | None = Query(default="Dubai", max_length=64),
    store: StoreDep = ...,  # type: ignore[assignment]
    cache: MarketCacheDep = ...,  # type: ignore[assignment]
) -> HeatmapActivityResponse:
    """Return per-area sale counts and median PPSF for a shared city window."""
    if lookback_days not in _ALLOWED_LOOKBACK_DAYS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "Please check the highlighted inputs.",
                "details": ["lookback_days: Input should be 180, 365, or 730"],
            },
        )

    snapshot = store.get_active()
    if snapshot is None:
        return HeatmapActivityResponse(
            date_range=DateRange(),
            items=[],
            data_state="degraded",
            source=_SOURCE,
        )

    emirate_norm = _norm(emirate) if emirate else ""
    cache_key = cache.build_key(
        "heatmap-activity",
        snapshot.version,
        {"emirate": emirate_norm, "lookback_days": lookback_days},
    )
    cached = cache.get_model(cache_key, HeatmapActivityResponse)
    if cached is not None:
        return cached

    result = compute_heatmap_activity(
        snapshot,
        emirate=emirate,
        lookback_days=lookback_days,
    )
    response = HeatmapActivityResponse(
        date_range=DateRange.model_validate({"from": result.date_from, "to": result.date_to}),
        items=[
            HeatmapAreaFact(
                area=row.area,
                label=row.label,
                transaction_count=row.transaction_count,
                median_ppsf=row.median_ppsf,
            )
            for row in result.items
        ],
        data_state=snapshot.state,
        source=_SOURCE,
    )
    cache.set_model(cache_key, response)
    return response

