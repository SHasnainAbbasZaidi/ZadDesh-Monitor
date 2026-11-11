from __future__ import annotations

import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from zaddesh_core.database import get_db
from zaddesh_core.models.checks import Check, CheckTemplate, CheckState
from zaddesh_core.models.infrastructure import Device, DeviceCredential
from zaddesh_core.models.auth import User
from zaddesh_core.schemas.check import (
    CheckCreate,
    CheckUpdate,
    CheckRead,
    CheckTemplateCreate,
    CheckTemplateRead,
    SnmpParameters,
)
from pydantic import ValidationError
from zaddesh_core.api.deps import require_role

router = APIRouter(prefix="", tags=["Checks & Monitoring"])


def validate_snmp_params(params: dict, *, stored: bool = False) -> None:
    try:
        parsed = SnmpParameters.model_validate(params)
    except ValidationError:
        raise HTTPException(422, "Invalid SNMP parameters. Submit credentials through Add Device.") from None
    if (parsed.credential is None) != stored:
        raise HTTPException(422, "SNMP requires its configured credential source")



# --- CHECK TEMPLATES ---
@router.get("/check-templates", response_model=List[CheckTemplateRead])
def list_templates(
    check_type: Optional[str] = None,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    q = db.query(CheckTemplate)
    if check_type:
        q = q.filter(CheckTemplate.check_type == check_type)
    templates = q.order_by(CheckTemplate.name.asc()).all()
    return [CheckTemplateRead.model_validate(item).model_copy(update={"default_params": {}})
            if current_user.role != "admin" else item for item in templates]


@router.post("/check-templates", response_model=CheckTemplateRead, status_code=status.HTTP_201_CREATED)
def create_template(
    payload: CheckTemplateCreate,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    if payload.check_type == "snmp":
        validate_snmp_params(payload.default_params)
    template = CheckTemplate(**payload.model_dump())
    db.add(template)
    db.commit()
    db.refresh(template)
    return template


# --- CHECKS ---
@router.get("/checks", response_model=List[CheckRead])
def list_checks(
    device_id: Optional[int] = None,
    check_type: Optional[str] = None,
    is_enabled: Optional[bool] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    q = db.query(Check)
    if device_id:
        q = q.filter(Check.device_id == device_id)
    if check_type:
        q = q.filter(Check.check_type == check_type)
    if is_enabled is not None:
        q = q.filter(Check.is_enabled == is_enabled)
    checks = q.order_by(Check.name.asc()).offset(offset).limit(limit).all()
    return [public_check(check, current_user) for check in checks]


@router.post("/checks", response_model=CheckRead, status_code=status.HTTP_201_CREATED)
def create_check(
    payload: CheckCreate,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    device = db.query(Device).filter(Device.id == payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if payload.check_type == "snmp":
        validate_snmp_params(payload.params)
    check = Check(**payload.model_dump())
    db.add(check)
    db.flush()

    state = CheckState(
        check_id=check.id,
        current_status="unknown",
        consecutive_failures=0,
        flap_detected=False,
        message="Created, awaiting first poll",
    )
    db.add(state)
    db.commit()
    db.refresh(check)
    return check


@router.get("/checks/{check_id}", response_model=CheckRead)
def get_check(
    check_id: int,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    check = db.query(Check).filter(Check.id == check_id).first()
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")
    return public_check(check, current_user)


def public_check(check: Check, user: User) -> CheckRead:
    result = CheckRead.model_validate(check)
    if user.role != "admin":
        return result.model_copy(update={"params": {}, "target": "(restricted)"})
    if check.check_type == "snmp":
        result.params.pop("stored_credential_id", None)
    return result


@router.put("/checks/{check_id}", response_model=CheckRead)
def update_check(
    check_id: int,
    payload: CheckUpdate,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    check = db.query(Check).filter(Check.id == check_id).first()
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")

    if check.check_type == "snmp" and payload.params is not None:
        credential_id = check.params.get("stored_credential_id")
        validate_snmp_params(payload.params, stored=credential_id is not None)
        if credential_id is not None:
            if any(payload.params.get(field) != check.params.get(field) for field in ("version", "security_level")):
                raise HTTPException(422, "The SNMP version and security level must match the stored credentials")
            payload.params["stored_credential_id"] = credential_id
    for k, v in payload.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(check, k, v)
    check.updated_at = datetime.datetime.utcnow()
    db.commit()
    db.refresh(check)
    return public_check(check, current_user)


@router.delete("/checks/{check_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_check(
    check_id: int,
    current_user: User = Depends(require_role(["admin"])),
    db: Session = Depends(get_db),
):
    check = db.query(Check).filter(Check.id == check_id).first()
    if not check:
        raise HTTPException(status_code=404, detail="Check not found")
    if check.check_type == "snmp" and (credential_id := check.params.get("stored_credential_id")) is not None:
        db.query(DeviceCredential).filter_by(
            id=credential_id, device_id=check.device_id, credential_type="snmp",
        ).delete()
    db.delete(check)
    db.commit()

