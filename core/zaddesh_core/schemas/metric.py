from __future__ import annotations

import datetime
from typing import Optional, List, Dict
from pydantic import BaseModel, Field


class MetricPoint(BaseModel):
    device_id: int = Field(gt=0)
    check_id: Optional[int] = None
    metric_key: str = Field(min_length=1, max_length=128)
    timestamp: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    value: float = Field(allow_inf_nan=False)
    tags: Dict[str, str] = Field(default_factory=dict, max_length=32)


class MetricIngestBatch(BaseModel):
    collector_id: Optional[int] = None
    points: List[MetricPoint] = Field(max_length=5000)


class MetricQueryRequest(BaseModel):
    device_id: int = Field(gt=0)
    metric_keys: List[str] = Field(min_length=1, max_length=24)
    check_id: Optional[int] = None
    start_time: datetime.datetime
    end_time: datetime.datetime = Field(default_factory=datetime.datetime.utcnow)
    step_seconds: Optional[int] = 60


class MetricSeriesPoint(BaseModel):
    timestamp: datetime.datetime
    value: float


class MetricSeries(BaseModel):
    metric_key: str
    device_id: int
    check_id: Optional[int] = None
    points: List[MetricSeriesPoint]


class MetricQueryResponse(BaseModel):
    series: List[MetricSeries]


class ProbeLatencyDevice(BaseModel):
    id: int
    name: str
    ip: str
    status: str


class ProbeLatencyChannel(BaseModel):
    device_id: int
    check_name: str
    probe_down: bool
    interval_seconds: int
    latency: list[MetricSeriesPoint] = Field(default_factory=list)
    failures: list[datetime.datetime] = Field(default_factory=list)


class ProbeLatencyResponse(BaseModel):
    devices: list[ProbeLatencyDevice]
    channels: list[ProbeLatencyChannel]
    start: datetime.datetime
    end: datetime.datetime
    bucket_seconds: int

