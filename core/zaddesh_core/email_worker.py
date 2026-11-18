"""Durable, read-only Zabbix event polling and SMTP delivery."""

import asyncio
from collections import defaultdict
from hashlib import sha256
import logging
import re
from time import time
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import SQLAlchemyError
from pymongo.errors import PyMongoError

from zaddesh_core.config import settings
from zaddesh_core.database import SessionLocal
from zaddesh_core.email_alerts import (HISTORY_DAYS, PENDING, QUEUE_LIMIT, matches_ports, matching_recipient, matching_rule, render,
                                       scheduled_for, template_default)
from zaddesh_core.email_ports import PORT_FIELDS, port_target
from zaddesh_core.email_smtp import SMTPOutcome, smtp_operation
from zaddesh_core.infrastructure_monitoring import classify, identifier, objects
from zaddesh_core.models.email_alerts import EmailAttempt, EmailDelivery, EmailProblem, EmailRecipient, EmailRule, EmailSettings, EmailTemplate
from zaddesh_core.models.zabbix import ZabbixConnection
from zaddesh_core.models.infrastructure import Device
from zaddesh_core.security import decrypt_secret

logger = logging.getLogger(__name__)
PROBLEM_LIMIT = 5000
POLL_SECONDS = 25
SEND_SECONDS = 5
LEASE_SECONDS = 600
RECOVERY_WINDOW = 7 * 86400
EVENT_FIELDS = ["eventid", "objectid", "name", "severity", "clock", "suppressed", "r_eventid", "value"]
PROBLEM_PATTERNS = (("vpn_failure", r"\bvpn\b|ipsec"), ("interface_errors", r"packet.loss|interface.*(?:error|discard)|flapping"),
                    ("link_down", r"\b(?:link|interface|wan|uplink)\b.*\b(?:down|failed)\b"),
                    ("service_stopped", r"service.*(?:stopped|not running|failed|down)"),
                    ("hardware", r"\b(?:raid|fan|ups|battery|temperature)\b|power supply|disk failure"))


def configured_items(db, model):
    return [{**row.config, "id": row.id} for row in db.scalars(select(model).order_by(model.id))]


def pending_count(db):
    return db.scalar(select(func.count()).select_from(EmailDelivery).where(EmailDelivery.status.in_(PENDING))) or 0


def config_lock(db):
    # All producers hold the same row lock before checking/enforcing the queue limit.
    db.execute(update(EmailSettings).where(EmailSettings.id == 1).values(id=EmailSettings.id))


def make_delivery(db, config, event, recipient, kind, now, problem_key=None, slot="", rule=None):
    address = recipient["email"]
    discriminator = rule["id"] if rule and rule["allow_duplicates"] else ""
    key = sha256(str((problem_key or uuid4().hex, kind, slot, address.casefold(), discriminator)).encode()).hexdigest()
    existing = db.scalar(select(EmailDelivery).where(EmailDelivery.dedup_key == key))
    if existing is not None:
        return existing, False
    template_kind = ("critical" if event["severity"] >= 4 else "warning") if kind in {"opened", "test"} else kind
    template = db.get(EmailTemplate, template_kind)
    content = render(template.config if template else template_default(template_kind), event, now, config["timezone"], kind == "test")
    notice = EmailDelivery(dedup_key=key, problem_key=problem_key, recipient_id=recipient["id"], address=address,
                           kind=kind, severity=event["severity"], created_at=now, next_attempt=now,
                           subject=content.pop("subject"), payload={**content, "device": event["name"], "ip": event["ip"],
                           "problem": event["problem"], "event_id": event["event_id"], "recovery_event_id": event.get("recovery_event_id"),
                           "rule_id": rule["id"] if rule else None})
    db.add(notice)
    db.flush()
    return notice, True


def numeric(value, limit=20):
    value = str(value)
    return int(value) if value.isascii() and value.isdecimal() and len(value) <= limit else None


def event_time(row, now):
    clock = numeric(row.get("clock"), 10)
    return clock if clock is not None and 0 < clock <= now + 30 else None


def batches(values, size=100):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def zabbix_connection(db):
    from zaddesh_core.api.v1.zabbix import saved_api
    return saved_api(db)


def attach_ports(api, events):
    from zaddesh_core.api.v1.zabbix import visible

    trigger_ids = sorted({str(row["objectid"]) for row in events if identifier(row.get("objectid"))})
    ports_by_trigger = {}
    try:
        for batch in batches(trigger_ids, 50):
            triggers = api.rows("trigger.get", {"triggerids": batch, "output": ["triggerid"], "selectItems": PORT_FIELDS})
            for trigger in triggers:
                trigger_id = str(trigger.get("triggerid"))
                if trigger_id in batch and isinstance(trigger.get("items"), list):
                    ports_by_trigger[trigger_id] = {port["id"]: {**port, "name": visible(port["name"], api.token)}
                                                    for item in objects(trigger["items"]) if (port := port_target(item))}
    except HTTPException:
        return False
    for row in events:
        if (ports := ports_by_trigger.get(str(row.get("objectid")))) is not None:
            row["_email_ports"] = list(ports.values())
    return all(trigger_id in ports_by_trigger for trigger_id in trigger_ids)


def fetch_zabbix(db, since, cursor, active_ids, pending_ports=None):
    from zaddesh_core.api.v1.zabbix import HOST_FIELDS

    with zabbix_connection(db) as api:
        initial = {"source": 0, "object": 0, "value": 1, "time_from": since, "output": EVENT_FIELDS,
                   "selectHosts": ["hostid", "host", "name"], "selectTags": ["tag", "value"], "sortfield": "eventid",
                   "sortorder": "ASC", "limit": 100}
        if cursor is not None:
            initial["eventid_from"] = str(int(cursor) + 1)
        new_events = api.rows("event.get", initial)
        updates = api.rows("event.get", {"eventids": active_ids, "output": EVENT_FIELDS}) if active_ids else []
        recovery_ids = sorted({str(row["r_eventid"]) for row in [*new_events, *updates]
                               if identifier(row.get("r_eventid")) and str(row["r_eventid"]) != "0"})
        recovered = {}
        for batch in batches(recovery_ids):
            for row in api.rows("event.get", {"eventids": batch, "value": 0, "output": ["eventid", "clock", "value"]}):
                if str(row.get("eventid")) in batch and str(row.get("value")) == "0":
                    recovered[str(row["eventid"])] = row
        host_ids = sorted({str(host["hostid"]) for row in new_events for host in objects(row.get("hosts")) if identifier(host.get("hostid"))})
        inventory = {}
        group_parameter = "selectHostGroups" if tuple(map(int, api.version.split(".")[:2])) >= (6, 2) else "selectGroups"
        for batch in batches(host_ids):
            for row in api.rows("host.get", {**HOST_FIELDS, group_parameter: ["groupid", "name"], "selectTags": ["tag", "value"],
                                             "selectParentTemplates": ["name"], "hostids": batch}):
                if str(row.get("hostid")) in batch:
                    inventory[str(row["hostid"])] = row
        ports_ok = pending_ports is None or attach_ports(api, [*new_events, *(row for row in updates if str(row.get("eventid")) in pending_ports)])
        return new_events, updates, recovered, inventory, api.token, ports_ok


def context_for(row, inventory, token, detected_at):
    from zaddesh_core.api.v1.incidents import zabbix_category
    from zaddesh_core.api.v1.zabbix import host_interfaces, visible

    hosts = [inventory.get(str(host.get("hostid")), host) for host in objects(row.get("hosts"))]
    names = [visible(host.get("name") or host.get("host"), token) for host in hosts]
    addresses = [visible(item.get("ip"), token) for host in hosts for item in sorted(host_interfaces(host),
                 key=lambda value: str(value.get("main")) != "1") if item.get("ip")]
    groups = sorted({str(group["groupid"]) for host in hosts for group in objects(host.get("hostgroups", host.get("groups")))
                     if identifier(group.get("groupid"))})
    tags = defaultdict(list)
    for tag in objects(row.get("tags")) + [tag for host in hosts for tag in objects(host.get("tags"))]:
        key = visible(tag.get("tag"), token)
        value = visible(tag.get("value"), token)
        if key and value not in tags[key] and len(tags[key]) < 20:
            tags[key].append(value)
    title = visible(row.get("name"), token)
    name = ", ".join(dict.fromkeys(filter(None, names)))[:512]
    problem_type = zabbix_category(title, name)
    if problem_type == "other":
        problem_type = next((kind for kind, pattern in PROBLEM_PATTERNS if re.search(pattern, title, re.I)), "other")
    return {"name": name, "ip": addresses[0] if addresses else "", "category": classify(hosts[0]) if hosts else "Other Devices",
            "problem": title, "problem_type": problem_type, "detected_at": detected_at, "event_id": str(row["eventid"]),
            "host_ids": [str(host["hostid"]) for host in hosts if identifier(host.get("hostid"))], "host_group_ids": groups,
            "tags": dict(tags)}


def action_slots(rule, problem, age):
    if rule["opened"]:
        yield "opened", ""
    if (rule["escalate_severity"] and problem.severity > problem.notified_severity
            and problem.severity == problem.context.get("peak_severity")):
        yield "escalation", str(problem.severity)
    if rule["escalation_minutes"] and age >= rule["escalation_minutes"]:
        yield "escalation", "elapsed"
    due = [threshold for threshold in rule["reminder_minutes"] if age >= threshold]
    if rule["repeat_minutes"] and age >= rule["repeat_minutes"]:
        due.append(age // rule["repeat_minutes"] * rule["repeat_minutes"])
    if due:
        yield "reminder", str(max(due))


def priority_target(db, context):
    address = context.get("ip")
    name = context.get("name", context.get("device", ""))
    query = db.query(Device).filter(Device.is_priority.is_(True))
    if address:
        return query.filter(or_(Device.ip == address, Device.hostname == address)).first() is not None
    return bool(name and query.filter(Device.name == name).first() is not None)


def schedule_problem(db, config, problem, rules, recipients, now, capacity):
    if not priority_target(db, problem.context):
        return capacity
    context = {**problem.context, "severity": problem.severity, "resolved_at": problem.resolved_at}
    if problem.suppressed and problem.resolved_at is None:
        return capacity
    age = ((problem.resolved_at or now) - context["detected_at"]) // 60
    accepted = defaultdict(set)
    if problem.resolved_at:
        for row in db.scalars(select(EmailDelivery).where(EmailDelivery.problem_key == problem.key, EmailDelivery.status == "sent",
                                                        EmailDelivery.kind != "recovery")):
            accepted[row.address.casefold()].add(row.payload["rule_id"])
    for rule in rules:
        matches = matching_rule(rule, context, age)
        if not rule["enabled"] or not matches_ports(rule, context) or (not matches and not problem.resolved_at):
            continue
        for recipient in recipients:
            if capacity <= 0:
                return capacity
            if not matching_recipient(rule, recipient, context) or not scheduled_for(rule, recipient, config, context, now):
                continue
            previously_sent = accepted.get(recipient["email"].casefold(), set())
            if problem.resolved_at:
                if not (previously_sent and rule["recovery"] and (matches or rule["id"] in previously_sent)):
                    continue
                actions = [("recovery", "")]
            else:
                actions = action_slots(rule, problem, age)
            for kind, slot in actions:
                if capacity <= 0:
                    return capacity
                _, created = make_delivery(db, config, context, recipient, kind, now, problem.key, slot, rule)
                capacity -= int(created)
    return capacity


def observe(db, state, events, recovered, inventory, token, now, is_new, capacity, rules, recipients):
    for row in events:
        event_id = str(row.get("eventid"))
        if not identifier(event_id) or str(row.get("value")) != "1":
            continue
        clock, severity = event_time(row, now), numeric(row.get("severity"), 1)
        if clock is None or severity not in range(6) or (is_new and clock < state.scan_from):
            continue
        key = f"{state.source}:{event_id}"
        problem = db.get(EmailProblem, key)
        if problem is None:
            if not is_new:
                continue
            problem = EmailProblem(key=key, source=state.source, event_id=event_id, observed_at=now, severity=severity,
                                   notified_severity=severity, context=context_for(row, inventory, token, clock))
            db.add(problem)
        elif problem.resolved_at is not None:
            continue
        problem.observed_at = now
        problem.severity = severity
        problem.suppressed = str(row.get("suppressed")) == "1"
        problem.context = {**problem.context, "peak_severity": max(severity, problem.context.get("peak_severity", -1))}
        if "_email_ports" in row:
            problem.context = {**problem.context, "ports": row["_email_ports"]}
        recovery_id = str(row.get("r_eventid"))
        if recovery_id in recovered:
            stamp = event_time(recovered[recovery_id], now)
            if stamp is not None and stamp >= clock:
                problem.resolved_at = stamp
                problem.context = {**problem.context, "recovery_event_id": recovery_id}
                db.execute(update(EmailDelivery).where(EmailDelivery.problem_key == key,
                    EmailDelivery.status.in_(("queued", "retrying")), EmailDelivery.kind != "recovery").values(
                        status="cancelled", last_error="Problem recovered before sending"))
        # If Zabbix reports a recovery ID but its timestamp is unavailable, wait for the actual recovery event.
        if not identifier(recovery_id) or recovery_id == "0" or problem.resolved_at is not None:
            capacity = schedule_problem(db, state.config, problem, rules, recipients, now, capacity)
        else:
            problem.suppressed = True
    return capacity


def poll_once(now=None):
    from zaddesh_core.api.v1.zabbix import source_key

    now = int(time()) if now is None else now
    with SessionLocal() as db:
        state = db.get(EmailSettings, 1)
        if state is None or not state.config.get("enabled") or state.poll_after > now:
            return
        generation = state.generation
        lease = now + LEASE_SECONDS
        claimed = db.execute(update(EmailSettings).where(EmailSettings.id == 1, EmailSettings.poll_after <= now,
                             EmailSettings.generation == generation).values(poll_after=lease)).rowcount
        if not claimed:
            return
        connection = db.get(ZabbixConnection, 1)
        if connection is None:
            state.poll_after, state.poll_error = now + POLL_SECONDS, "Connect Zabbix to send infrastructure notifications"
            db.commit()
            return
        source = source_key(connection.api_url)
        if state.source != source:
            state.source, state.cursor = source, None
            state.scan_from = max(state.scan_from, now if state.last_poll else 0)
        since, cursor = state.scan_from, state.cursor
        db.commit()
        active = db.execute(select(EmailProblem.event_id, EmailProblem.context).where(EmailProblem.source == source,
            EmailProblem.resolved_at.is_(None)).order_by(EmailProblem.observed_at, EmailProblem.event_id).limit(200)).all()
        rules = configured_items(db, EmailRule)
        pending_ports = {event_id for event_id, context in active if "ports" not in context} if any(
            rule["enabled"] and rule.get("ports") for rule in rules) else None
        try:
            events, updates, recovery, inventory, token, ports_ok = fetch_zabbix(db, since, cursor, [event_id for event_id, _ in active], pending_ports)
        except HTTPException:
            db.rollback()
            db.execute(update(EmailSettings).where(EmailSettings.id == 1, EmailSettings.generation == generation,
                       EmailSettings.poll_after == lease).values(poll_after=now + POLL_SECONDS, poll_error="Could not read Zabbix events. Check the saved connection and API permissions."))
            db.commit()
            return
        config_lock(db)
        db.refresh(state)
        if state.generation != generation or state.poll_after != lease:
            return
        recipients, rules = configured_items(db, EmailRecipient), configured_items(db, EmailRule)
        capacity = QUEUE_LIMIT - pending_count(db)
        remaining = PROBLEM_LIMIT - (db.scalar(select(func.count()).select_from(EmailProblem)) or 0)
        # Keep the cursor at the last accepted event when the observation budget is full.
        intake = events[:max(0, remaining)] if capacity > 0 else []
        if intake:
            capacity = observe(db, state, intake, recovery, inventory, token, now, True, capacity, rules, recipients)
            highest = max((number for row in intake if (number := numeric(row.get("eventid"))) is not None), default=0)
            if highest:
                state.cursor = str(max(highest, int(cursor or 0)))
        capacity = observe(db, state, updates, recovery, inventory, token, now, False, capacity, rules, recipients)
        for problem in db.scalars(select(EmailProblem).where(EmailProblem.source == source, EmailProblem.resolved_at >= now - RECOVERY_WINDOW)
                                  .order_by(EmailProblem.observed_at).limit(100)):
            problem.observed_at = now
            capacity = schedule_problem(db, state.config, problem, rules, recipients, now, capacity)
        state.poll_after, state.last_poll = now + POLL_SECONDS, now
        state.poll_error = None
        if len(intake) < len(events):
            state.poll_error = "Notification queue or event intake is full. Intake will resume as capacity opens."
        elif not ports_ok:
            state.poll_error = "Could not read interface items for some Zabbix triggers. Check trigger.get API permissions."
        db.commit()


def native_email_exists(db, notice):
    target = notice.payload.get("recovery_event_id") if notice.kind == "recovery" else notice.payload["event_id"]
    with zabbix_connection(db) as api:
        alerts = api.rows("alert.get", {"eventids": [target], "filter": {"alerttype": 0, "status": [0, 1, 3]},
            "output": ["eventid", "sendto", "status"], "selectMediatypes": ["type"], "limit": 1000})
    for alert in alerts:
        if str(alert.get("eventid")) != target or notice.address.casefold() not in {
            address.strip().casefold() for address in str(alert.get("sendto", "")).replace(",", ";").split(";")
        }:
            continue
        if not objects(alert.get("mediatypes")):
            raise HTTPException(502, "Zabbix email media type is unavailable")
        if any(str(media.get("type")) == "0" for media in alert["mediatypes"]):
            return True
    return False


def credential_for(state):
    return decrypt_secret(state.secret) if state.config["auth"] != "none" else ""


def finish(db, notice, outcome, generation, now):
    current = db.get(EmailSettings, 1, populate_existing=True)
    db.refresh(notice)
    if notice.status != "sending":
        return
    notice.last_error = outcome.message
    if outcome.result == "accepted":
        notice.status, notice.sent_at = "sent", now
    elif outcome.result == "cancelled":
        notice.status = "cancelled"
        notice.attempts -= 1
    elif outcome.result == "deferred":
        notice.status, notice.next_attempt = "retrying", now + 60
        notice.attempts -= 1
        db.commit()
        return
    elif outcome.result == "temporary" and notice.attempts < 5:
        notice.status, notice.next_attempt = "retrying", now + 30 * 2 ** max(0, notice.attempts - 1)
    else:
        notice.status, notice.uncertain = "failed", outcome.result == "uncertain"
    if outcome.result != "cancelled":
        db.add(EmailAttempt(delivery_id=notice.id, at=now, status=notice.status, outcome=outcome.message))
    if current and current.generation == generation and outcome.result != "cancelled":
        if current.health != "auth_error" or outcome.result == "authentication":
            current.health = "connected" if outcome.result == "accepted" else "auth_error" if outcome.result == "authentication" else "error"
            current.last_error = outcome.message
    db.commit()


def send_once(now=None):
    from zaddesh_core.api.v1.zabbix import source_key

    deterministic = now is not None
    now = int(time()) if now is None else now
    with SessionLocal() as db:
        state = db.get(EmailSettings, 1)
        if state is None or state.send_after > now:
            return
        if state.prune_after > now and db.scalar(select(EmailDelivery.id).where(
            EmailDelivery.status.in_(PENDING), EmailDelivery.next_attempt <= now).limit(1)) is None:
            return
        config_lock(db)
        db.refresh(state)
        if state.send_after > now:
            return
        for expired in db.scalars(select(EmailDelivery).where(EmailDelivery.status == "sending", EmailDelivery.next_attempt <= now).limit(200)):
            expired.status, expired.uncertain = "failed", True
            expired.last_error = "Interrupted SMTP attempt. Verify SMTP logs before retrying."
            db.add(EmailAttempt(delivery_id=expired.id, at=now, status="failed", outcome=expired.last_error))
        if state.prune_after <= now:
            cutoff = now - HISTORY_DAYS * 86400
            db.execute(delete(EmailDelivery).where(EmailDelivery.created_at < cutoff, EmailDelivery.status.not_in(PENDING)))
            db.execute(delete(EmailProblem).where(or_(EmailProblem.resolved_at < cutoff,
                                                     EmailProblem.observed_at < cutoff)))
            state.prune_after = now + 86400
        if state.health == "auth_error":
            db.commit()
            return
        query = select(EmailDelivery).where(EmailDelivery.status.in_(("queued", "retrying")), EmailDelivery.next_attempt <= now)
        if not state.config.get("enabled"):
            query = query.where(EmailDelivery.kind == "test")
        notice = db.scalar(query.order_by(EmailDelivery.next_attempt, EmailDelivery.id).limit(1))
        if notice is None:
            db.commit()
            return
        if notice.kind != "test" and not priority_target(db, notice.payload):
            notice.status, notice.last_error = "cancelled", "Device priority is disabled"
            db.commit()
            return
        recipient = db.get(EmailRecipient, notice.recipient_id) if notice.recipient_id else None
        if recipient is None or not recipient.config["enabled"] or recipient.address_key != notice.address.casefold():
            notice.status, notice.last_error = "cancelled", "Recipient was disabled, removed, or changed"
            db.commit()
            return
        if notice.problem_key:
            problem = db.get(EmailProblem, notice.problem_key)
            connection = db.get(ZabbixConnection, 1)
            if not problem or not connection or problem.source != source_key(connection.api_url) or (problem.resolved_at and notice.kind != "recovery"):
                notice.status, notice.last_error = "cancelled", "Event is no longer eligible to send"
                db.commit()
                return
            rule = db.get(EmailRule, notice.payload["rule_id"])
            if not rule or not rule.config["enabled"]:
                notice.status, notice.last_error = "cancelled", "Alert rule is no longer enabled"
                db.commit()
                return
            event = {**problem.context, "severity": problem.severity}
            if not matches_ports(rule.config, event) or (notice.kind != "recovery" and not (matching_rule(rule.config, event, (now - event["detected_at"]) // 60)
                and matching_recipient(rule.config, {**recipient.config, "id": recipient.id}, event))):
                notice.status, notice.last_error = "cancelled", "The current rule or recipient filters no longer match"
                db.commit()
                return
            if (notice.kind != "recovery" and (problem.suppressed or problem.observed_at < now - 300)) or not scheduled_for(
                rule.config, recipient.config, state.config, {"severity": notice.severity}, now,
            ):
                notice.next_attempt = now + 60
                db.commit()
                return
        check_native = bool(notice.problem_key and state.config["avoid_native"])
        notice.status, notice.next_attempt = "sending", now + LEASE_SECONDS
        notice.attempts += 1
        state.send_after = now + SEND_SECONDS
        generation, config = state.generation, dict(state.config)
        mail = {key: getattr(notice, key) for key in ("address", "subject", "created_at", "dedup_key", "payload")}
        try:
            secret = credential_for(state)
        except (RuntimeError, ValueError, InvalidTag):
            secret = None
        delivery_id = notice.id
        db.commit()
    outcome = None
    if check_native:
        with SessionLocal() as db:
            notice = db.get(EmailDelivery, delivery_id)
            try:
                if notice and native_email_exists(db, notice):
                    outcome = SMTPOutcome("cancelled", "Native Zabbix email already scheduled or sent to this recipient")
            except HTTPException:
                outcome = SMTPOutcome("deferred", "Could not verify native Zabbix email. Check alert and media API permissions.")
    if outcome is None:
        outcome = (smtp_operation(config, secret, mail) if secret is not None else
                   SMTPOutcome("authentication", "Could not decrypt the SMTP credential. Re-enter it in Email Server Settings."))
    with SessionLocal() as db:
        notice = db.get(EmailDelivery, delivery_id)
        if notice is not None:
            finish(db, notice, outcome, generation, now if deterministic else int(time()))


async def email_loop(stop):
    async def run(job, interval):
        while not stop.is_set():
            try:
                await asyncio.to_thread(job)
            except (SQLAlchemyError, PyMongoError, ValueError, OSError):
                logger.warning("Email notification worker could not complete a cycle")
            try:
                await asyncio.wait_for(stop.wait(), interval)
            except asyncio.TimeoutError:
                pass
    if not settings.demo_mode:
        await asyncio.gather(run(poll_once, 5), run(send_once, 2))
