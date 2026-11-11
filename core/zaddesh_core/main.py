from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from collections import OrderedDict
from threading import Lock
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from zaddesh_core.config import settings
import zaddesh_core.models  # noqa: F401
from zaddesh_core.api.v1 import api_v1_router
from zaddesh_core.api.v1.network_diagram import topology_loop
from zaddesh_core.api.v1.system import router as system_root_router
from zaddesh_core.incident_delivery import delivery_loop


@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop = asyncio.Event()
    worker = (asyncio.create_task(delivery_loop(stop)) if settings.features.incident_workspace
              and settings.features.incident_webhook_url and not settings.demo_mode else None)
    topology = (asyncio.create_task(topology_loop(stop)) if settings.features.network_diagram_beta
                and not settings.demo_mode else None)
    try:
        yield
    finally:
        stop.set()
        await asyncio.gather(*(task for task in (worker, topology) if task is not None))


app = FastAPI(
    title="ZadDesh Monitoring Platform",
    description="Self-hosted IT infrastructure monitoring server and API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # Validation errors for credentials must never echo the submitted payload.
    return JSONResponse({"detail": [
        {key: error[key] for key in ("type", "loc", "msg")} for error in exc.errors()
    ]}, status_code=422)


# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.server.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Local process guard; enforce distributed limits at the reverse proxy as well.
request_windows: OrderedDict[tuple[str, str], tuple[float, int]] = OrderedDict()
request_windows_lock = Lock()
LIMITS = {"/api/v1/auth/login": 12, "/api/v1/auth/setup": 12,
          "/api/v1/metrics/ingest": 600, "/api/v1/collectors/register": 20, "/api/v1/logs/ingest": 600}


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start_time = time.monotonic()
    path = request.url.path.rstrip("/")
    if request.method == "POST" and path in LIMITS:
        length = request.headers.get("content-length", "")
        if not length.isascii() or not length.isdecimal():
            return JSONResponse({"detail": "Content-Length is required"}, status_code=411)
        max_length = {
            "/api/v1/metrics/ingest": 4 * 1024 * 1024,
            "/api/v1/logs/ingest": 1024 * 1024,
        }.get(path, 32 * 1024)
        if len(length) > 9 or int(length) > max_length:
            return JSONResponse({"detail": "Request body too large"}, status_code=413)
        client = request.client.host if request.client else "unknown"
        key = (path, client)
        with request_windows_lock:
            opened, count = request_windows.get(key, (start_time, 0))
            if start_time - opened >= 60:
                opened, count = start_time, 0
            if count >= LIMITS[path]:
                return JSONResponse({"detail": "Too many requests"}, status_code=429,
                                    headers={"Retry-After": str(max(1, int(60 - (start_time - opened))))})
            request_windows[key] = (opened, count + 1)
            request_windows.move_to_end(key)
            if len(request_windows) > 4096:
                request_windows.popitem(last=False)
    response: Response = await call_next(request)
    process_time = (time.monotonic() - start_time) * 1000
    response.headers["X-Process-Time-Ms"] = f"{process_time:.2f}"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# Mount Root System Endpoints & API v1
app.include_router(system_root_router)
app.include_router(api_v1_router)

# Locate and serve built static files from React frontend if present
web_dist_candidates = [
    Path.cwd() / "web" / "dist",
    Path.cwd().parent / "web" / "dist",
    Path("/usr/share/zaddesh/web"),
]

web_dist_path: Path | None = None
for candidate in web_dist_candidates:
    if candidate.exists() and (candidate / "index.html").exists():
        web_dist_path = candidate
        break

if web_dist_path:
    index_file = web_dist_path / "index.html"
    # Serve assets folder
    assets_dir = web_dist_path / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    # Fallback to index.html for SPA client-side routing
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if full_path.startswith(("api/", "assets/")) or ".." in full_path.replace("\\", "/").split("/"):
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(index_file, headers={"Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"})
else:
    @app.get("/", include_in_schema=False)
    def index_placeholder():
        return {
            "name": "ZadDesh Monitoring Core",
            "version": "0.1.0",
            "status": "online",
            "api_docs": "/docs",
            "api_v1": "/api/v1",
        }


