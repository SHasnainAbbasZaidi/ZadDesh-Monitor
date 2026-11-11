from __future__ import annotations

import datetime
from typing import Optional, Dict, Any, Literal, Self
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


class SnmpParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credential: str | None = Field(default=None, min_length=1, max_length=48, pattern=r"^[A-Za-z0-9_]+$")
    version: Literal["1", "2c", "3"] = "3"
    operation: Literal["get", "getnext", "getbulk", "walk"] = "get"
    oid: str = Field(default="1.3.6.1.2.1.1.3.0", max_length=123, pattern=r"^\.?[0-9]+(\.[0-9]+){1,63}$")
    port: int = Field(default=161, ge=1, le=65535)
    security_level: Literal["noAuthNoPriv", "authNoPriv", "authPriv"] | None = None
    auth_protocol: Literal["md5", "sha1", "sha224", "sha256", "sha384", "sha512"] | None = None
    privacy_protocol: Literal["des", "aes128", "aes192", "aes256"] | None = None
    context_name: str | None = Field(default=None, max_length=32)
    max_repetitions: int | None = Field(default=None, ge=1, le=64)

    @model_validator(mode="after")
    def check_operation(self) -> Self:
        if self.version == "1" and self.operation == "getbulk":
            raise ValueError("GETBULK requires SNMP v2c or v3")
        return self


class SnmpCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    community: SecretStr | None = Field(default=None, min_length=1, max_length=255)
    username: SecretStr | None = Field(default=None, min_length=1, max_length=32)
    auth_password: SecretStr | None = Field(default=None, min_length=8, max_length=255)
    priv_password: SecretStr | None = Field(default=None, min_length=8, max_length=255)


class InitialSnmpCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    params: SnmpParameters
    interval_seconds: int = Field(default=60, ge=5, le=86400)
    connect_to: Literal["ip", "dns"] = "ip"
    credentials: SnmpCredentials | None = None

    @model_validator(mode="after")
    def check_credentials(self) -> Self:
        credentials = self.credentials
        if (credentials is None) == (self.params.credential is None):
            raise ValueError("Provide SNMP credentials or a collector alias")
        if credentials is None:
            return self
        provided = credentials.model_fields_set
        if self.params.version == "3":
            level = self.params.security_level or "authPriv"
            required = {"username"}
            if level != "noAuthNoPriv":
                required.add("auth_password")
            if level == "authPriv":
                required.add("priv_password")
        else:
            required = {"community"}
        if provided != required or any(getattr(credentials, field) is None for field in required):
            raise ValueError("SNMP credentials must match the selected version and security level")
        return self


class CheckTemplateBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    check_type: str = Field(pattern="^(icmp|http|https|tcp|udp|snmp)$")
    description: Optional[str] = None
    default_params: Dict[str, Any] = Field(default_factory=dict)
    default_interval: int = 60
    default_timeout: int = 5
    default_retries: int = 3


class CheckTemplateCreate(CheckTemplateBase):
    pass


class CheckTemplateRead(CheckTemplateBase):
    id: int
    created_at: datetime.datetime

    model_config = {"from_attributes": True}


class CheckStateRead(BaseModel):
    check_id: int
    current_status: str
    last_check_time: Optional[datetime.datetime] = None
    last_change_time: Optional[datetime.datetime] = None
    consecutive_failures: int
    flap_detected: bool
    message: Optional[str] = None

    model_config = {"from_attributes": True}


class CheckBase(BaseModel):
    device_id: int
    template_id: Optional[int] = None
    name: str = Field(min_length=1, max_length=128)
    check_type: str = Field(pattern="^(icmp|http|https|tcp|udp|snmp)$")
    target: str = Field(min_length=1, max_length=255)
    params: Dict[str, Any] = Field(default_factory=dict)
    interval_seconds: int = Field(default=60, ge=5, le=86400)
    timeout_seconds: int = Field(default=5, ge=1, le=120)
    retries: int = Field(default=3, ge=0, le=5)
    is_enabled: bool = True


class CheckCreate(CheckBase):
    check_type: str = Field(pattern="^(icmp|http|https|tcp|snmp)$")


class CheckUpdate(BaseModel):
    name: Optional[str] = None
    target: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    interval_seconds: Optional[int] = Field(default=None, ge=5, le=86400)
    timeout_seconds: Optional[int] = Field(default=None, ge=1, le=120)
    retries: Optional[int] = Field(default=None, ge=0, le=5)
    is_enabled: Optional[bool] = None


class CheckRead(CheckBase):
    id: int
    created_at: datetime.datetime
    updated_at: datetime.datetime
    state: Optional[CheckStateRead] = None

    model_config = {"from_attributes": True}

