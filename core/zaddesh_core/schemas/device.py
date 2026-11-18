from __future__ import annotations

import datetime
from typing import Optional, List, Literal
from pydantic import BaseModel, Field
from zaddesh_core.schemas.check import InitialSnmpCheck


class SiteBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=100)
    description: Optional[str] = None
    address: Optional[str] = None
    latitude: Optional[str] = None
    longitude: Optional[str] = None


class SiteCreate(SiteBase):
    pass


class SiteRead(SiteBase):
    id: int
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class GroupBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=1, max_length=100)
    parent_id: Optional[int] = None
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class GroupCreate(GroupBase):
    pass


class GroupRead(GroupBase):
    id: int
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class DeviceBase(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    ip: str = Field(min_length=1, max_length=64)
    hostname: Optional[str] = None
    site_id: Optional[int] = None
    group_id: Optional[int] = None
    os: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    is_monitored: bool = True
    is_priority: bool = False
    collection_mode: Literal["scheduled", "on_demand"] = "scheduled"


class DeviceCreate(DeviceBase):
    initial_check_types: List[Literal["icmp", "http", "https", "tcp"]] = Field(default=["icmp"], max_length=4)
    initial_snmp: InitialSnmpCheck | None = None
    initial_tcp_port: int = Field(default=80, ge=1, le=65535)


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    hostname: Optional[str] = None
    site_id: Optional[int] = None
    group_id: Optional[int] = None
    status: Optional[str] = None
    os: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    is_monitored: Optional[bool] = None
    is_priority: Optional[bool] = None
    collection_mode: Optional[Literal["scheduled", "on_demand"]] = None


class DeviceRead(DeviceBase):
    id: int
    status: str
    uptime_seconds: int
    last_seen: Optional[datetime.datetime] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}


class DeviceDetail(DeviceRead):
    site: Optional[SiteRead] = None
    group: Optional[GroupRead] = None
    check_count: int = 0
    checks_up: int = 0
    checks_down: int = 0


class DeviceStats(BaseModel):
    total_devices: int
    up: int
    down: int
    warning: int
    unknown: int
    unmanaged: int

