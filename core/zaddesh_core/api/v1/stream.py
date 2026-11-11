"""Authenticated change notifications for the single-process core."""
from __future__ import annotations

import asyncio
from threading import Lock
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.responses import StreamingResponse

from zaddesh_core.api.deps import get_current_user, security_bearer
from zaddesh_core.database import SessionLocal

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
