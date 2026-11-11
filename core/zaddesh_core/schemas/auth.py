from __future__ import annotations

import datetime
from typing import Literal, Optional
from pydantic import BaseModel, EmailStr, Field, SecretStr, field_validator

UserRole = Literal["admin", "viewer"]


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserRead"


class TokenPayload(BaseModel):
    sub: str
    user_id: int
    role: str
    exp: int


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1, max_length=1024)


class SetupRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr
    password: str = Field(min_length=8, max_length=1024)
    full_name: Optional[str] = None


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    email: EmailStr
    password: SecretStr = Field(min_length=12, max_length=1024)
    full_name: Optional[str] = None
    role: UserRole = "viewer"
    is_active: bool = True


class UserUpdate(BaseModel):
    password: SecretStr | None = Field(default=None, min_length=12, max_length=1024)
    current_password: SecretStr | None = Field(default=None, min_length=1, max_length=1024)
    role: UserRole | None = None
    is_active: Optional[bool] = None


class UserRead(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str] = None
    role: UserRole
    is_active: bool
    created_at: datetime.datetime

    model_config = {"from_attributes": True}

    @field_validator("role", mode="before")
    @classmethod
    def map_legacy_role(cls, value: str) -> UserRole:
        return "admin" if value == "admin" else "viewer"


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: Optional[int] = None


class ApiTokenRead(BaseModel):
    id: int
    name: str
    prefix: str
    created_at: datetime.datetime
    expires_at: Optional[datetime.datetime] = None
    last_used_at: Optional[datetime.datetime] = None

    model_config = {"from_attributes": True}


class ApiTokenCreatedResponse(ApiTokenRead):
    raw_token: str

