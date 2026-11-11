from __future__ import annotations

import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class AlertAckRequest(BaseModel):
    comment: str = Field(min_length=1, max_length=500)


class AlertRead(BaseModel):
    id: int
    rule_id: Optional[int] = None
    device_id: int
    check_id: Optional[int] = None
    severity: str
    status: str
    title: str
    message: Optional[str] = None
    value: Optional[float] = None
    triggered_at: datetime.datetime
    resolved_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}


class EventRead(BaseModel):
    id: int
    device_id: Optional[int] = None
    check_id: Optional[int] = None
    event_type: str
    severity: str
    title: str
    message: Optional[str] = None
    details: Dict[str, Any]
    timestamp: datetime.datetime

    model_config = {"from_attributes": True}

