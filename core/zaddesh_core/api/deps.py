from __future__ import annotations

import datetime
import secrets
from typing import Optional, List
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from zaddesh_core.database import get_db
from zaddesh_core.security import decode_access_token, hash_token
from zaddesh_core.config import settings
from zaddesh_core.models.auth import User, ApiToken
from zaddesh_core.models.infrastructure import Collector

security_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    db: Session = Depends(get_db),
) -> User:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Case 1: API Token starting with zds_
    if token.startswith("zds_"):
        token_hash = hash_token(token)
        api_token = (
            db.query(ApiToken)
            .filter(ApiToken.token_hash == token_hash)
            .first()
        )
        if not api_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API token",
            )
        if api_token.expires_at and api_token.expires_at < datetime.datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API token has expired",
            )
        # Update last_used_at
        api_token.last_used_at = datetime.datetime.utcnow()
        db.commit()

        user = db.query(User).filter(User.id == api_token.user_id, User.is_active.is_(True)).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User associated with token is not active or found",
            )
        return user

    # Case 2: JWT Access Token
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("user_id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    user = db.query(User).filter(User.id == user_id, User.is_active.is_(True)).first()
    if not user or payload.get("session_version", 0) != user.session_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or user inactive",
        )

    return user


def require_role(allowed_roles: List[str]):
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles and current_user.role != "admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Requires one of roles: {allowed_roles}",
            )
        return current_user

    return role_checker


def verify_collector_bootstrap(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
) -> None:
    if settings.demo_mode:
        raise HTTPException(status_code=403, detail="Collectors are disabled in demo mode")
    secret = settings.collector.enrollment_token
    if not secret or not credentials or not secrets.compare_digest(
        credentials.credentials.encode(), secret.encode()
    ):
        raise HTTPException(status_code=401, detail="Collector enrollment authorization required")


def verify_collector_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_bearer),
    db: Session = Depends(get_db),
) -> Collector | None:
    if settings.demo_mode:
        raise HTTPException(status_code=403, detail="Collectors are disabled in demo mode")
    if credentials:
        token = credentials.credentials
        shared_secret = settings.collector.shared_secret
        if shared_secret and secrets.compare_digest(token.encode(), shared_secret.encode()):
            return None  # Explicitly configured fleet-wide credential.
        collector = db.query(Collector).filter(Collector.auth_token_hash == hash_token(token)).first()
        if collector:
            return collector
    raise HTTPException(status_code=401, detail="Invalid collector token")
