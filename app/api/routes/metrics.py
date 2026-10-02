"""Prometheus scrape endpoint. Public, like /healthz and /readyz."""
from fastapi import APIRouter, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.observability.metrics import refresh_seats_available

router = APIRouter(tags=["observability"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request):
    await refresh_seats_available(getattr(request.app.state, "pool", None))
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
