from __future__ import annotations

import datetime
from typing import Optional, Dict
from pydantic import BaseModel, Field


class SystemInfo(BaseModel):
    version: str
    status: str
    environment: str
    is_setup_required: bool
    database_type: str
    uptime_seconds: float
    counts: Dict[str, int]


class CollectorRegister(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    auth_token: str = Field(min_length=32, max_length=256, repr=False)
    site_id: Optional[int] = None
    version: str = "0.1.0"
    hostname: Optional[str] = None
    ip_address: Optional[str] = None


class CollectorHeartbeat(BaseModel):
    collector_id: int
    status: str = "online"
    metrics_queued: int = 0
    checks_active: int = 0


class CollectorRead(BaseModel):
    id: int
    name: str
    site_id: Optional[int] = None
    version: str
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    status: str
    last_heartbeat_at: Optional[datetime.datetime] = None
    registered_at: datetime.datetime

    model_config = {"from_attributes": True}

