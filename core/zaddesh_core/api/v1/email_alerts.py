import csv
from datetime import date, datetime, time as day_time
from io import StringIO
from time import time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from cryptography.exceptions import InvalidTag
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import AwareDatetime, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from zaddesh_core.api.deps import require_role
from zaddesh_core.api.v1.zabbix import saved_api, visible
from zaddesh_core.config import settings
from zaddesh_core.database import get_db
from zaddesh_core.email_alerts import (KIND, QUEUE_LIMIT, SEVERITIES, VARIABLES, Input, RecipientInput, RuleInput,
                                       ServerInput, Simulation, TemplateInput, matching_recipient, matching_rule, render,
                                       scheduled_for, simulation_event, template_default)
from zaddesh_core.email_smtp import smtp_operation
from zaddesh_core.email_ports import monitored_ports
from zaddesh_core.email_worker import configured_items, config_lock, credential_for, make_delivery, pending_count
from zaddesh_core.models.auth import AuditLog, User
from zaddesh_core.models.email_alerts import EmailAttempt, EmailDelivery, EmailRecipient, EmailRule, EmailSettings, EmailTemplate
from zaddesh_core.models.zabbix import ZabbixConnection
from zaddesh_core.security import allows_secret_transport, encrypt_secret

admin = require_role(["admin"])
router = APIRouter(prefix="/email-alerts", dependencies=[Depends(admin)], tags=["Email Alerts & Notifications"])
TYPE = Literal["queued", "sending", "sent", "failed", "retrying", "cancelled"]
TEMPLATE_KINDS = ("critical", "warning", "recovery", "escalation", "reminder")


def audit(db, user, action, item):
    db.add(AuditLog(user_id=user.id, action=f"email.{action}", entity_type="email_alerts", entity_id=str(item), details={}))


def state_or_error(db):
    state = db.get(EmailSettings, 1)
    if state is None:
        raise HTTPException(409, "Save the Email Server Settings first")
    return state


def get_credential(state):
    try:
        return credential_for(state)
    except (ValueError, RuntimeError, InvalidTag):
        raise HTTPException(409, "Could not decrypt the SMTP credential. Re-enter it in Email Server Settings.") from None


def safe_settings(state):
    if state is None:
        return {"config": None, "has_credential": False, "health": "disabled", "last_error": None, "last_test": None,
                "last_poll": None, "poll_error": None}
    return {"config": state.config, "has_credential": bool(state.secret), "health": state.health,
            "last_error": state.last_error, "last_test": state.last_test, "last_poll": state.last_poll, "poll_error": state.poll_error}


def no_demo():
    if settings.demo_mode:
        raise HTTPException(409, "SMTP and automated notifications are disabled in demo mode")


def secure_transport(request: Request):
    if not allows_secret_transport(request):
        raise HTTPException(403, "Use HTTPS or local loopback to save the SMTP credential")


@router.get("")
def workspace(db: Session = Depends(get_db)):
    state = db.get(EmailSettings, 1)
    recipients, rules = configured_items(db, EmailRecipient), configured_items(db, EmailRule)
    start = int(datetime.now(ZoneInfo(state.config["timezone"] if state else "UTC")).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    accepted = db.scalar(select(func.count()).select_from(EmailDelivery).where(EmailDelivery.sent_at >= start))
    failures = db.scalar(select(func.count()).select_from(EmailAttempt).where(EmailAttempt.status.in_(("retrying", "failed")), EmailAttempt.at >= start))
    return {"server": safe_settings(state), "recipients": recipients, "rules": rules, "variables": VARIABLES,
            "templates": {kind: row.config if (row := db.get(EmailTemplate, kind)) else template_default(kind) for kind in TEMPLATE_KINDS},
            "zabbix_configured": db.get(ZabbixConnection, 1) is not None,
            "stats": {"recipients": sum(row["enabled"] for row in recipients), "rules": sum(row["enabled"] for row in rules),
                      "accepted_today": accepted, "failed_attempts_today": failures, "pending": pending_count(db)}}


@router.put("/server", dependencies=[Depends(secure_transport)])
def save_server(payload: ServerInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    if payload.enabled:
        no_demo()
    config_lock(db)
    state = db.get(EmailSettings, 1)
    if state is None:
        state = EmailSettings(id=1, config={}, secret="", generation=0)
        db.add(state)
    previous = state.config
    credential = state.secret
    bound_fields = ("host", "port", "encryption", "auth", "username")
    config = payload.model_dump(exclude={"credential", "clear_credential"})
    changed_target = any(previous.get(key) != config[key] for key in bound_fields)
    if payload.clear_credential or payload.auth == "none" or changed_target:
        credential = ""
    if payload.credential:
        try:
            credential = encrypt_secret(payload.credential.get_secret_value())
        except RuntimeError:
            raise HTTPException(503, "Configure the core encryption key before saving SMTP credentials") from None
    if payload.auth != "none" and not credential:
        raise HTTPException(422, "Provide the password, app password, or OAuth access token")
    state.config, state.secret = config, credential
    credentials_changed = changed_target or previous.get("timeout") != config["timeout"] or bool(payload.credential)
    state.generation += 1
    state.poll_after = 0
    if credentials_changed:
        state.health, state.last_error, state.last_test = "untested", None, None
    if payload.enabled and not previous.get("enabled"):
        state.scan_from = int(time())
        state.cursor = None
    audit(db, user, "server_updated", 1)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "SMTP settings were changed concurrently. Reload and retry.") from None
    return safe_settings(state)


def reserve_test(db, now):
    state_or_error(db)
    if not db.execute(update(EmailSettings).where(EmailSettings.id == 1, EmailSettings.test_after <= now).values(test_after=now + 10)).rowcount:
        raise HTTPException(429, "Wait ten seconds between diagnostic requests")


@router.post("/test-connection")
def test_connection(user: User = Depends(admin), db: Session = Depends(get_db)):
    no_demo()
    now = int(time())
    reserve_test(db, now)
    state = state_or_error(db)
    generation, config, credential = state.generation, state.config, get_credential(state)
    audit(db, user, "smtp_connection_tested", 1)
    db.commit()
    outcome = smtp_operation(config, credential)
    health = "connected" if outcome.result == "connected" else "auth_error" if outcome.result == "authentication" else "error"
    db.execute(update(EmailSettings).where(EmailSettings.id == 1, EmailSettings.generation == generation).values(
        health=health, last_test=now, last_error=outcome.message))
    db.commit()
    return {"ok": outcome.result == "connected", "message": outcome.message or "SMTP connection and authentication succeeded"}


def find(db, model, item_id):
    row = db.get(model, item_id)
    if row is None:
        raise HTTPException(404, "Email notification record was not found")
    return row


def save_recipient(db, user, payload, item_id=None):
    key = str(payload.email).casefold()
    duplicate = db.scalar(select(EmailRecipient.id).where(EmailRecipient.address_key == key))
    if duplicate is not None and duplicate != item_id:
        raise HTTPException(409, "This email address is already in the recipient directory")
    record = find(db, EmailRecipient, item_id) if item_id else EmailRecipient()
    record.address_key, record.config = key, payload.model_dump()
    db.add(record)
    try:
        db.flush()
        audit(db, user, "recipient_updated", record.id)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This email address is already in the recipient directory") from None
    return {**record.config, "id": record.id}


@router.post("/recipients", status_code=201)
def create_recipient(payload: RecipientInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    return save_recipient(db, user, payload)


@router.put("/recipients/{item_id}")
def update_recipient(item_id: int, payload: RecipientInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    return save_recipient(db, user, payload, item_id)


@router.delete("/recipients/{item_id}", status_code=204)
def delete_recipient(item_id: int, user: User = Depends(admin), db: Session = Depends(get_db)):
    record = find(db, EmailRecipient, item_id)
    for rule in db.scalars(select(EmailRule)):
        if item_id in rule.config["recipient_ids"]:
            keep = [value for value in rule.config["recipient_ids"] if value != item_id]
            rule.config = {**rule.config, "recipient_ids": keep,
                           "enabled": rule.config["enabled"] and bool(keep or rule.config["groups"])}
    db.delete(record)
    audit(db, user, "recipient_removed", item_id)
    db.commit()


def save_rule(db, user, payload, item_id=None):
    recipients = configured_items(db, EmailRecipient)
    employee_ids = {item["id"] for item in recipients}
    groups = {name.casefold() for item in recipients for name in [item["team"], *item["groups"]] if name}
    if not set(payload.recipient_ids) <= employee_ids or not {name.casefold() for name in payload.groups} <= groups:
        raise HTTPException(422, "Select employees or notification groups that exist in the recipient directory")
    record = find(db, EmailRule, item_id) if item_id else EmailRule()
    config = payload.model_dump()
    existing_ports = {port["id"]: port for port in (record.config or {}).get("ports", [])}
    requested = config["ports"]
    new_hosts = {port["host_id"] for port in requested if port["id"] not in existing_ports}
    if new_hosts:
        with saved_api(db) as api:
            for host in sorted(new_hosts):
                existing_ports.update({port["id"]: {**port, "name": visible(port["name"], api.token)}
                                       for port in monitored_ports(api, host)})
    if any(port["id"] not in existing_ports or existing_ports[port["id"]]["host_id"] != port["host_id"] for port in requested):
        raise HTTPException(422, "Select interfaces currently monitored on the chosen Zabbix hosts")
    config["ports"] = [existing_ports[port["id"]] for port in requested]
    record.config = config
    db.add(record)
    db.flush()
    audit(db, user, "rule_updated", record.id)
    db.commit()
    return {**record.config, "id": record.id}


@router.post("/rules", status_code=201)
def create_rule(payload: RuleInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    return save_rule(db, user, payload)


@router.put("/rules/{item_id}")
def update_rule(item_id: int, payload: RuleInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    return save_rule(db, user, payload, item_id)


@router.delete("/rules/{item_id}", status_code=204)
def delete_rule(item_id: int, user: User = Depends(admin), db: Session = Depends(get_db)):
    db.delete(find(db, EmailRule, item_id))
    audit(db, user, "rule_removed", item_id)
    db.commit()


@router.get("/targets")
def targets(db: Session = Depends(get_db)):
    if not db.get(ZabbixConnection, 1):
        return {"hosts": [], "groups": []}
    with saved_api(db) as api:
        result = {}
        for label, method, identifier in (("hosts", "host.get", "hostid"), ("groups", "hostgroup.get", "groupid")):
            rows = api.rows(method, {"output": [identifier, "name"], "sortfield": "name" if label == "groups" else "host", "limit": 1000})
            result[label] = [{"id": str(row[identifier]), "name": visible(row.get("name"), api.token)} for row in rows if identifier in row]
        return result


@router.get("/targets/{host_id}/ports")
def target_ports(host_id: Annotated[str, Field(pattern=r"^[0-9]{1,20}$")], db: Session = Depends(get_db)):
    with saved_api(db) as api:
        return {"ports": [{**port, "name": visible(port["name"], api.token)} for port in monitored_ports(api, host_id)]}


@router.put("/templates/{kind}")
def save_template(kind: KIND, payload: TemplateInput, user: User = Depends(admin), db: Session = Depends(get_db)):
    template = db.get(EmailTemplate, kind) or EmailTemplate(kind=kind)
    template.config = payload.model_dump()
    db.add(template)
    audit(db, user, "template_updated", kind)
    db.commit()
    return template.config


class Preview(Input):
    event: Simulation
    kind: KIND = "critical"
    template: TemplateInput | None = None


@router.post("/preview")
def preview(payload: Preview, db: Session = Depends(get_db)):
    state = db.get(EmailSettings, 1)
    zone = state.config["timezone"] if state else "UTC"
    template = db.get(EmailTemplate, payload.kind)
    content = payload.template.model_dump() if payload.template else template.config if template else template_default(payload.kind)
    now = int(time())
    event = simulation_event(payload.event, now)
    if payload.kind == "recovery":
        event["resolved_at"] = now
    return render(content, event, now, zone, test=True)


@router.post("/simulate")
def simulate(payload: Simulation, db: Session = Depends(get_db)):
    state = db.get(EmailSettings, 1)
    config = state.config if state else {"timezone": "UTC", "quiet_hours": None}
    now = int(time())
    event = simulation_event(payload, now)
    recipients = configured_items(db, EmailRecipient)
    outcomes = []
    for rule in configured_items(db, EmailRule):
        if matching_rule(rule, event, payload.duration_minutes):
            outcomes.append({"rule": rule["name"], "recipients": [{"name": recipient["name"], "email": recipient["email"],
                "scheduled": scheduled_for(rule, recipient, config, event, now)} for recipient in recipients if matching_recipient(rule, recipient, event)]})
    return {"matches": outcomes}


class TestEmail(Input):
    recipient_ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=20)
    event: Simulation = Field(default_factory=Simulation)


@router.post("/test-email", status_code=202)
def send_test_email(payload: TestEmail, user: User = Depends(admin), db: Session = Depends(get_db)):
    no_demo()
    now = int(time())
    reserve_test(db, now)
    state = state_or_error(db)
    if state.health == "auth_error":
        raise HTTPException(409, "Correct and retest the SMTP authentication settings before queuing mail")
    get_credential(state)
    recipients = [find(db, EmailRecipient, item_id) for item_id in set(payload.recipient_ids)]
    if not all(recipient.config["enabled"] for recipient in recipients):
        raise HTTPException(422, "Choose enabled recipients from the directory")
    if pending_count(db) + len(recipients) > QUEUE_LIMIT:
        raise HTTPException(429, "Notification queue is full")
    event = simulation_event(payload.event, now)
    delivery_ids = [make_delivery(db, state.config, event, {**recipient.config, "id": recipient.id}, "test", now)[0].id for recipient in recipients]
    audit(db, user, "test_email_queued", 1)
    db.commit()
    return {"queued": delivery_ids}


def history_item(row):
    return {**{key: getattr(row, key) for key in ("id", "address", "kind", "status", "severity", "created_at", "sent_at", "next_attempt",
                                                 "subject", "attempts", "last_error", "uncertain")},
            **{key: row.payload.get(key) for key in ("device", "ip", "problem", "event_id")}}


def history_zone(db):
    state = db.get(EmailSettings, 1)
    return ZoneInfo(state.config["timezone"] if state else "UTC")


def date_boundary(value, zone, end=False):
    if not isinstance(value, datetime):
        value = datetime.combine(value, day_time.max if end else day_time.min, zone)
    return int(value.timestamp())


def filtered_history(q, status, severity, start, end, zone):
    query = select(EmailDelivery)
    if q:
        query = query.where(or_(EmailDelivery.address.icontains(q, autoescape=True), EmailDelivery.subject.icontains(q, autoescape=True),
                               EmailDelivery.payload["device"].as_string().icontains(q, autoescape=True)))
    if status is not None:
        query = query.where(EmailDelivery.status == status)
    if severity is not None:
        query = query.where(EmailDelivery.severity == severity)
    if start is not None:
        query = query.where(EmailDelivery.created_at >= date_boundary(start, zone))
    if end is not None:
        query = query.where(EmailDelivery.created_at <= date_boundary(end, zone, end=True))
    return query.order_by(EmailDelivery.created_at.desc(), EmailDelivery.id.desc())


@router.get("/history")
def history(q: str = Query("", max_length=120), status: TYPE | None = None, severity: int | None = Query(None, ge=0, le=5),
            start: date | AwareDatetime | None = None, end: date | AwareDatetime | None = None, before: int = Query(0, ge=0),
            limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    query = filtered_history(q, status, severity, start, end, history_zone(db))
    if before:
        query = query.where(EmailDelivery.id < before)
    rows = list(db.scalars(query.limit(limit + 1)))
    return {"items": [history_item(row) for row in rows[:limit]], "next": rows[limit - 1].id if len(rows) > limit else None}


def csv_text(value):
    text = "" if value is None else str(value)
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text


@router.get("/history.csv")
def history_csv(q: str = Query("", max_length=120), status: TYPE | None = None, severity: int | None = Query(None, ge=0, le=5),
                start: date | AwareDatetime | None = None, end: date | AwareDatetime | None = None, db: Session = Depends(get_db)):
    output = StringIO()
    writer = csv.writer(output)
    fields = ("created_at", "device", "ip", "problem", "severity", "address", "subject", "kind", "status", "attempts", "last_error")
    writer.writerow(fields)
    zone = history_zone(db)
    for row in db.scalars(filtered_history(q, status, severity, start, end, zone).limit(10000)):
        value = history_item(row)
        value["created_at"] = datetime.fromtimestamp(row.created_at, zone).isoformat()
        value["severity"] = SEVERITIES[row.severity] if row.severity is not None else ""
        writer.writerow([csv_text(value[key]) for key in fields])
    return Response(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="zaddesh-email-history.csv"'})


@router.get("/history/{item_id}")
def delivery_detail(item_id: int, db: Session = Depends(get_db)):
    row = find(db, EmailDelivery, item_id)
    attempts = db.scalars(select(EmailAttempt).where(EmailAttempt.delivery_id == row.id).order_by(EmailAttempt.id.desc()).limit(100))
    return {**history_item(row), "message": {key: row.payload.get(key) for key in ("text", "html")},
            "attempt_history": [{"at": attempt.at, "status": attempt.status, "outcome": attempt.outcome} for attempt in attempts]}


class Retry(Input):
    confirm_uncertain: bool = False


@router.post("/history/{item_id}/retry", status_code=202)
def retry_delivery(item_id: int, payload: Retry, user: User = Depends(admin), db: Session = Depends(get_db)):
    no_demo()
    config_lock(db)
    state = state_or_error(db)
    row = find(db, EmailDelivery, item_id)
    if row.status != "failed" or (row.uncertain and not payload.confirm_uncertain):
        raise HTTPException(409, "Retry an unsuccessful attempt. Verify ambiguous SMTP acceptance before retrying.")
    if state.health == "auth_error":
        raise HTTPException(409, "Correct and retest the SMTP authentication settings before retrying")
    if pending_count(db) >= QUEUE_LIMIT:
        raise HTTPException(429, "Notification queue is full")
    row.status, row.next_attempt, row.uncertain, row.last_error = "queued", int(time()), False, None
    row.attempts = 0
    audit(db, user, "delivery_requeued", row.id)
    db.commit()
    return {"id": row.id, "status": row.status}
