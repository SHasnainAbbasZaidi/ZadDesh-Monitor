"""Authenticated change notifications for the single-process core."""
from __future__ import annotations

import asyncio
from threading import Lock
import time
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Security, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse

from zaddesh_core.api.deps import get_current_user, security_bearer
from zaddesh_core.database import SessionLocal
from zaddesh_core.config import settings

router = APIRouter(tags=["Live updates"])
_state = Lock()
_revision = 0
_clients = 0


def notify_change() -> None:
    global _revision
    with _state:
        _revision += 1


def authenticate_stream(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security_bearer)],
) -> None:
    # Authenticate once, and release the DB connection before opening the stream.
    with SessionLocal() as session:
        get_current_user(credentials, session)


@router.get("/stream")
async def live_updates(request: Request, _: Annotated[None, Depends(authenticate_stream)]):
    """Send invalidation events; clients fetch fresh snapshots using their bearer token."""
    with _state:
        if _clients >= 64:
            raise HTTPException(503, "Live stream capacity reached", headers={"Retry-After": "10"})

    async def events():
        global _clients
        with _state:
            _clients += 1
            seen = _revision
        started = time.monotonic()
        last_message = started
        try:
            yield "event: ready\ndata: {}\n\n"
            while time.monotonic() - started < 120 and not await request.is_disconnected():
                await asyncio.sleep(.75)
                with _state:
                    current = _revision
                now = time.monotonic()
                if current != seen:
                    seen = current
                    last_message = now
                    yield "event: change\ndata: {}\n\n"
                elif now - last_message >= 10:
                    last_message = now
                    yield ": heartbeat\n\n"
        finally:
            with _state:
                _clients -= 1

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"})


@router.websocket("/addon-updates")
async def addon_updates(socket: WebSocket):
    global _clients
    origin = socket.headers.get("origin")
    offered = socket.scope.get("subprotocols", [])
    if (not (settings.features.incident_workspace or settings.features.network_diagram_beta)
        or (origin and urlsplit(origin).netloc != socket.headers.get("host"))
        or len(offered) != 2 or offered[0] != "zaddesh-v1"):
        await socket.close(code=1008)
        return
    try:
        await run_in_threadpool(authenticate_stream, HTTPAuthorizationCredentials(scheme="Bearer", credentials=offered[1]))
    except HTTPException:
        await socket.close(code=1008)
        return
    with _state:
        available = _clients < 64
        seen = _revision
        if available:
            _clients += 1
    if not available:
        await socket.close(code=1013)
        return
    try:
        await socket.accept(subprotocol="zaddesh-v1")
        await socket.send_json({"event": "ready"})
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            try:
                if (await asyncio.wait_for(socket.receive(), .75))["type"] == "websocket.disconnect":
                    return
            except asyncio.TimeoutError:
                pass
            with _state:
                current = _revision
            if current != seen:
                await socket.send_json({"event": "change"})
                seen = current
        await socket.close()
    except (WebSocketDisconnect, OSError):
        pass
    finally:
        with _state:
            _clients -= 1
