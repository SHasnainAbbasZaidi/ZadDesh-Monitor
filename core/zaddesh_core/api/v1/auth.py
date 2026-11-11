from __future__ import annotations

import datetime
from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from zaddesh_core.database import get_db
from zaddesh_core.config import settings
from zaddesh_core.models.auth import User, Role, ApiToken, AuditLog
from zaddesh_core.schemas.auth import (
    Token,
    LoginRequest,
    SetupRequest,
    UserCreate,
    UserRead,
    UserUpdate,
    ApiTokenCreate,
    ApiTokenRead,
    ApiTokenCreatedResponse,
)
from zaddesh_core.security import (
    hash_password,
    verify_password,
    create_access_token,
    generate_api_token,
)
from zaddesh_core.api.deps import get_current_user, require_role

router = APIRouter(prefix="/auth", tags=["Authentication & RBAC"])


@router.get("/status")
def get_auth_status(db: Session = Depends(get_db)):
    """Check if initial setup is required (no admin user exists)."""
    user_count = db.query(User).count()
    return {
        "setup_required": user_count == 0,
        "allow_registration": settings.security.allow_registration,
    }


@router.post("/setup", response_model=Token, status_code=status.HTTP_201_CREATED)
def initial_setup(payload: SetupRequest, request: Request, db: Session = Depends(get_db)):
    """Initial setup wizard: create the first Administrator user."""
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="Complete initial setup from the server’s loopback interface")
    user_count = db.query(User).count()
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Initial setup already completed. Please log in.",
        )

    # Ensure admin role exists
    admin_role = db.query(Role).filter(Role.name == "admin").first()
    if not admin_role:
        admin_role = Role(
            name="admin",
            description="System Administrator with full privileges",
            permissions=["*"],
        )
        db.add(admin_role)
        db.flush()

    user = User(
        username=payload.username.strip(),
        email=payload.email.lower().strip(),
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role="admin",
        role_id=admin_role.id,
        is_active=True,
    )
    db.add(user)

    audit = AuditLog(
        user_id=None,
        action="system.initial_setup",
        entity_type="user",
        entity_id=payload.username,
        details={"email": payload.email, "role": "admin"},
    )
    db.add(audit)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(
        data={"sub": user.username, "user_id": user.id, "role": user.role, "session_version": user.session_version}
    )
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.security.jwt_expiration_minutes * 60,
        user=UserRead.model_validate(user),
    )


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    """Authenticate with username/password and return JWT bearer token."""
    user = (
        db.query(User)
        .filter(
            (User.username == payload.username.strip())
            | (User.email == payload.username.lower().strip())
        )
        .first()
    )
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    access_token = create_access_token(
        data={"sub": user.username, "user_id": user.id, "role": user.role, "session_version": user.session_version}
    )
    return Token(
        access_token=access_token,
        token_type="bearer",
        expires_in=settings.security.jwt_expiration_minutes * 60,
        user=UserRead.model_validate(user),
    )


@router.get("/me", response_model=UserRead)
def get_current_user_profile(current_user: User = Depends(get_current_user)):
    """Return profile of the authenticated user."""
    return current_user


@router.get("/tokens", response_model=List[ApiTokenRead])
def list_api_tokens(
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """List API tokens for the authenticated user."""
    return (
        db.query(ApiToken)
        .filter(ApiToken.user_id == current_user.id)
        .order_by(ApiToken.created_at.desc())
        .all()
    )


@router.post("/tokens", response_model=ApiTokenCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_api_token(
    payload: ApiTokenCreate,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Generate a new personal API token (shown only once)."""
    raw_token, prefix, token_hash = generate_api_token()
    expires_at = None
    if payload.expires_in_days:
        expires_at = datetime.datetime.utcnow() + datetime.timedelta(days=payload.expires_in_days)

    api_token = ApiToken(
        user_id=current_user.id,
        name=payload.name.strip(),
        prefix=prefix,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(api_token)
    db.commit()
    db.refresh(api_token)

    return ApiTokenCreatedResponse(
        id=api_token.id,
        name=api_token.name,
        prefix=api_token.prefix,
        created_at=api_token.created_at,
        expires_at=api_token.expires_at,
        last_used_at=api_token.last_used_at,
        raw_token=raw_token,
    )


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_api_token(
    token_id: int,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Revoke an API token."""
    token = (
        db.query(ApiToken)
        .filter(ApiToken.id == token_id, ApiToken.user_id == current_user.id)
        .first()
    )
    if not token:
        raise HTTPException(status_code=404, detail="API token not found")
    db.delete(token)
    db.commit()


@router.get("/users", response_model=List[UserRead])
def list_users(
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """List all users (Admin only)."""
    return db.query(User).order_by(User.id.asc()).all()


@router.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    """Create a new user (Admin only)."""
    existing = (
        db.query(User)
        .filter(
            (User.username == payload.username.strip())
            | (User.email == payload.email.lower().strip())
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already exists",
        )

    role = db.query(Role).filter_by(name=payload.role).first()
    user = User(
        username=payload.username.strip(),
        email=payload.email.lower().strip(),
        hashed_password=hash_password(payload.password.get_secret_value()),
        full_name=payload.full_name,
        role=payload.role,
        role_id=role.id if role else None,
        is_active=payload.is_active,
    )
    db.add(user)
    db.add(AuditLog(user_id=current_user.id, action="user.created", entity_type="user",
                    entity_id=user.username, details={"role": user.role}))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Username or email already exists") from None
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    payload: UserUpdate,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    actor_id = current_user.id
    if db.get_bind().dialect.name == "sqlite":
        # Take SQLite's write lock before re-reading both accounts.
        db.execute(update(User).where(User.id == actor_id).values(updated_at=User.updated_at))
    accounts = {
        account.id: account
        for account in db.query(User).filter(User.id.in_([actor_id, user_id]))
        .order_by(User.id).with_for_update().populate_existing()
    }
    actor = accounts.get(actor_id)
    if actor is None or actor.role != "admin" or not actor.is_active:
        raise HTTPException(403, "Administrator access required")
    account = accounts.get(user_id)
    if account is None:
        raise HTTPException(404, "User not found")
    changes: dict[str, str | bool] = {}
    if payload.role is not None and account.role != payload.role:
        if user_id == actor_id:
            raise HTTPException(409, "You cannot change your own access level")
        role = db.query(Role).filter_by(name=payload.role).first()
        account.role = payload.role
        account.role_id = role.id if role else None
        changes["role"] = payload.role
    if payload.is_active is not None and account.is_active != payload.is_active:
        if user_id == actor_id:
            raise HTTPException(409, "You cannot disable your own account")
        account.is_active = payload.is_active
        changes["active"] = payload.is_active
    if payload.password is not None:
        if user_id == actor_id and (payload.current_password is None or not verify_password(
            payload.current_password.get_secret_value(), account.hashed_password,
        )):
            raise HTTPException(400, "Current password is incorrect")
        account.hashed_password = hash_password(payload.password.get_secret_value())
        changes["password_changed"] = True
    if changes:
        account.session_version += 1
        db.query(ApiToken).filter(ApiToken.user_id == user_id).delete()
        db.add(AuditLog(user_id=actor_id, action="user.updated", entity_type="user",
                        entity_id=str(user_id), details=changes))
        db.commit()
        db.refresh(account)
    return account

