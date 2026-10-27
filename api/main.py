"""MIADP REST API (read-only). Run: `python tasks.py api` -> http://127.0.0.1:8000/docs

Security posture: local development only. There are no write endpoints; before any
public deployment add authentication/authorization, TLS, rate limiting, and secrets
management (see docs/deployment.md).
"""

from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.routes import anomalies, cases, health, ingestion, networks, ports, vessels
from maritime import DISCLAIMER, __version__
from maritime.utils.logging import setup_logging

setup_logging()
logger = logging.getLogger("maritime.api")

app = FastAPI(
    title="Maritime Intelligence & Anomaly Detection Platform API",
    version=__version__,
    description=(
        f"**{DISCLAIMER}**\n\nRead-only access to validated NOAA AIS observations, "
        "derived trajectories, port proximity, detections and the relationship network. "
        "List endpoints are paginated with a hard cap; there is no bulk export."
    ),
    contact={"name": "Sean Tessman"},
    license_info={"name": "MIT"},
)


@app.middleware("http")
async def request_logging(request: Request, call_next):  # noqa: ANN001, ANN201
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    request.state.request_id = rid
    t0 = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "%s %s -> %s in %.1f ms [rid=%s]",
        request.method,
        request.url.path,
        response.status_code,
        ms,
        rid,
    )
    response.headers["x-request-id"] = rid
    return response


def _rid(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": "http_error", "detail": exc.detail, "request_id": _rid(request)},
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    detail = [
        {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
        for e in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "detail": detail, "request_id": _rid(request)},
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error [rid=%s]", _rid(request))
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "detail": "unexpected server error; see logs",
            "request_id": _rid(request),
        },
    )


for r in (health, vessels, anomalies, ports, networks, ingestion, cases):
    app.include_router(r.router)
