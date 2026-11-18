"""Reassemble bounded, read-only SNMP event-log extension records."""
import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from zaddesh_core.config import settings
from zaddesh_core.incidents import redact
from zaddesh_core.models.alerts import Event

EVENT_LINES = "1.3.6.1.4.1.8072.1.3.2.4.1.2.14.122.97.100.100.101.115.104.95.101.118.101.110.116.115."
LEVELS = {"emergency", "alert", "critical", "error", "warning", "notice", "info", "debug"}


def save_client_logs(db, device_id, check_id, values):
    available, error = False, None
    messages = {}
    for oid, line in values.items():
        if not oid.startswith(EVENT_LINES):
            continue
        if line == "status|ready":
            available = True
            continue
        if line.startswith("status|unavailable"):
            error = "Client event-log read access is unavailable"
            continue
        fields = line.split("|", 5)
        if len(fields) != 6:
            error = "An incomplete SNMP event-log record was received"
            continue
        key, timestamp, level, part, count, content = fields
        if level not in LEVELS or not part.isdecimal() or not count.isdecimal() or not 0 <= int(part) < int(count) <= 512:
            error = "An invalid SNMP event-log record was received"
            continue
        group = (key, timestamp, level, int(count))
        messages.setdefault(group, {})[int(part)] = content
    prepared = []
    now = datetime.now(timezone.utc)
    for (key, timestamp, level, count), pieces in messages.items():
        if len(pieces) != count:
            error = "Some client issue logs exceeded this snapshot's collection limit"
            continue
        try:
            identity = base64.b64decode(key, validate=True).decode("utf-8")
            message = base64.b64decode("".join(pieces[part] for part in range(count)), validate=True).decode("utf-8")
            at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if at.tzinfo is None or at > now + timedelta(minutes=5) or at < now - timedelta(days=settings.logs.retention_days):
                continue
        except (ValueError, UnicodeError):
            error = "An invalid client event timestamp or message was received"
            continue
        if len(message) > 8192:
            message = message[:8100] + "\n[Message exceeded the 8 KiB log limit]"
        fingerprint = sha256(f"{device_id}:{identity}".encode()).hexdigest()
        prepared.append((fingerprint, at.astimezone(timezone.utc).replace(tzinfo=None), level, redact(message)))
    existing = {key for key, in db.query(Event.client_event_key).filter(Event.client_event_key.in_([record[0] for record in prepared]))} if prepared else set()
    added = 0
    for key, at, level, message in prepared:
        if key not in existing:
            existing.add(key)
            added += 1
            db.add(Event(device_id=device_id, check_id=check_id, event_type="client_issue", severity=level, title=message.splitlines()[0][:255] or "Client issue",
                         message=message, timestamp=at, client_event_key=key, details={"source": "SNMP event-log extension"}))
    return {"client_logs_available": available, "client_logs_error": error, "client_logs_added": added}
