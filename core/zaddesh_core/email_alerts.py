"""Validation, routing, and safe template rendering for infrastructure email."""

from datetime import datetime
from html import escape
from html.parser import HTMLParser
import re
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, StringConstraints, field_validator, model_validator

from zaddesh_core.config import settings

SEVERITIES = ("Not Classified", "Information", "Warning", "Average", "High", "Disaster")
KIND = Literal["warning", "critical", "recovery", "escalation", "reminder"]
CATEGORY = Literal["Network", "Servers", "Routers", "Firewalls", "CCTV", "Other Devices"]
PROBLEM_TYPE = Literal["device_offline", "link_down", "cpu_usage", "memory_usage", "network_usage", "storage_shortage",
                       "interface_errors", "service_stopped", "hardware", "vpn_failure", "other"]
SEVERITY = Annotated[int, Field(ge=0, le=5)]
IDENTIFIER = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,20}$")]
NAME = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
CLOCK = Annotated[str, StringConstraints(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")]
PENDING = ("queued", "retrying", "sending")
QUEUE_LIMIT = 2000
HISTORY_DAYS = 90
VARIABLES = ("device_name", "device_ip", "device_category", "interface_name", "problem", "severity", "detected_at", "recovered_at",
             "duration", "event_id", "device_status", "dashboard_link")
PLACEHOLDER = re.compile(r"{{\s*([a-z_]+)\s*}}")


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Schedule(Input):
    days: list[Annotated[int, Field(ge=0, le=6)]] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    start: CLOCK = "00:00"
    end: CLOCK = "00:00"


class ServerConfig(Input):
    host: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=253)]
    port: int = Field(587, ge=1, le=65535)
    encryption: Literal["starttls", "tls", "none"] = "starttls"
    auth: Literal["none", "password", "oauth2"] = "none"
    username: str = Field("", max_length=254)
    sender: EmailStr
    sender_name: str = Field("ZadDesh Monitoring", max_length=100)
    reply_to: EmailStr | None = None
    timeout: int = Field(10, ge=2, le=20)
    enabled: bool = False
    timezone: str = Field("UTC", max_length=64)
    quiet_hours: Schedule | None = None
    avoid_native: bool = False

    @field_validator("host")
    @classmethod
    def valid_host(cls, value):
        from ipaddress import ip_address
        try:
            ip_address(value)
        except ValueError:
            if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", value) or ".." in value:
                raise ValueError("Enter an SMTP hostname or IP address") from None
        if "%" in value:
            raise ValueError("Scoped SMTP addresses are unsupported")
        return value

    @field_validator("sender_name", "username", "sender", "reply_to")
    @classmethod
    def valid_header(cls, value):
        if value and any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Control characters are unavailable in email fields")
        return value

    @field_validator("sender", "reply_to")
    @classmethod
    def ascii_mailbox(cls, value):
        if value and not value.isascii():
            raise ValueError("Use an ASCII SMTP mailbox")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Enter an installed IANA timezone such as Asia/Karachi") from None
        return value

    @model_validator(mode="after")
    def secure_auth(self):
        if self.auth != "none" and (self.encryption == "none" or not self.username.strip()):
            raise ValueError("SMTP authentication requires a username and encrypted transport")
        if self.auth == "oauth2" and not self.username.isascii():
            raise ValueError("OAuth2 requires an ASCII SMTP username")
        return self


class ServerInput(ServerConfig):
    credential: SecretStr | None = Field(None, max_length=4096, min_length=1)
    clear_credential: bool = False

    @model_validator(mode="after")
    def validate_credential(self):
        if self.credential and (self.clear_credential or self.auth == "none"):
            raise ValueError("Select an authentication method before setting its credential")
        if self.credential:
            value = self.credential.get_secret_value()
            if not value.isascii() or any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("SMTP credentials must contain printable ASCII characters")
        return self


class RecipientInput(Input):
    name: NAME
    email: EmailStr
    team: str = Field("", max_length=100)
    groups: list[NAME] = Field(default_factory=list, max_length=30)
    enabled: bool = True
    severities: list[SEVERITY] = Field(default_factory=list, max_length=6)
    host_group_ids: list[IDENTIFIER] = Field(default_factory=list, max_length=100)
    schedule: Schedule | None = None

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        if not value.isascii():
            raise ValueError("Use an ASCII SMTP mailbox")
        return value


class PortTarget(Input):
    id: Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
    host_id: IDENTIFIER
    name: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class RuleInput(Input):
    name: NAME
    enabled: bool = True
    severities: list[SEVERITY] = Field(default_factory=lambda: [4, 5], min_length=1, max_length=6)
    categories: list[CATEGORY] = Field(default_factory=list, max_length=6)
    host_ids: list[IDENTIFIER] = Field(default_factory=list, max_length=100)
    ports: list[PortTarget] = Field(default_factory=list, max_length=50)
    host_group_ids: list[IDENTIFIER] = Field(default_factory=list, max_length=100)
    tags: dict[NAME, Annotated[str, StringConstraints(max_length=200)]] = Field(default_factory=dict, max_length=20)
    problem_types: list[PROBLEM_TYPE] = Field(default_factory=list, max_length=11)
    minimum_minutes: int = Field(0, ge=0, le=10080)
    recipient_ids: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list, max_length=100)
    groups: list[NAME] = Field(default_factory=list, max_length=30)
    schedule: Schedule | None = None
    bypass_quiet: bool = False
    allow_duplicates: bool = False
    opened: bool = True
    recovery: bool = True
    escalate_severity: bool = True
    escalation_minutes: int | None = Field(None, ge=1, le=10080)
    reminder_minutes: list[Annotated[int, Field(ge=1, le=10080)]] = Field(default_factory=list, max_length=8)
    repeat_minutes: int | None = Field(None, ge=5, le=10080)

    @model_validator(mode="after")
    def validate_rule(self):
        if not (self.recipient_ids or self.groups):
            raise ValueError("Assign an employee or a notification group")
        if not (self.opened or self.recovery or self.escalate_severity or self.escalation_minutes or self.reminder_minutes or self.repeat_minutes):
            raise ValueError("Enable at least one notification condition")
        self.recipient_ids = sorted(set(self.recipient_ids))
        self.ports = list({port.id: port for port in self.ports}.values())
        if self.host_ids and any(port.host_id not in self.host_ids for port in self.ports):
            raise ValueError("Every selected port must belong to a host allowed by the rule")
        self.reminder_minutes = sorted(set(self.reminder_minutes))
        return self


class SafeHTML(HTMLParser):
    tags = {"p", "br", "hr", "div", "span", "strong", "em", "b", "i", "ul", "ol", "li", "h1", "h2", "h3",
            "table", "thead", "tbody", "tr", "td", "th", "blockquote", "pre", "code"}

    def handle_starttag(self, tag, attrs):
        if tag not in self.tags or attrs:
            raise ValueError("Email HTML accepts basic formatting tags without attributes")

    def handle_endtag(self, tag):
        self.handle_starttag(tag, [])

    def handle_decl(self, decl):
        raise ValueError("Email HTML cannot contain declarations")

    def handle_pi(self, data):
        raise ValueError("Email HTML cannot contain processing instructions")


class TemplateInput(Input):
    subject: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    text: Annotated[str, StringConstraints(min_length=1, max_length=10000)]
    html: str = Field("", max_length=16000)

    @model_validator(mode="after")
    def valid_template(self):
        if any(ord(character) < 32 or ord(character) == 127 for character in self.subject):
            raise ValueError("Email subjects cannot contain control characters")
        for value in (self.subject, self.text, self.html):
            if any(name not in VARIABLES for name in PLACEHOLDER.findall(value)) or "{{" in PLACEHOLDER.sub("", value):
                raise ValueError("Use only the listed template variables")
        parser = SafeHTML()
        parser.feed(self.html)
        parser.close()
        return self


class Simulation(Input):
    name: NAME = "Test device"
    ip: str = Field("", max_length=64)
    category: CATEGORY = "Network"
    problem: Annotated[str, StringConstraints(min_length=1, max_length=512)] = "Simulated infrastructure outage"
    severity: SEVERITY = 4
    problem_type: PROBLEM_TYPE = "device_offline"
    host_ids: list[IDENTIFIER] = Field(default_factory=list, max_length=20)
    ports: list[PortTarget] = Field(default_factory=list, max_length=1)
    host_group_ids: list[IDENTIFIER] = Field(default_factory=list, max_length=30)
    tags: dict[NAME, Annotated[str, StringConstraints(max_length=200)]] = Field(default_factory=dict, max_length=20)
    duration_minutes: int = Field(0, ge=0, le=525600)


def scheduled(schedule, local):
    if not schedule:
        return True
    clock = local.strftime("%H:%M")
    start, end, weekday = schedule["start"], schedule["end"], local.weekday()
    if start == end:
        return weekday in schedule["days"]
    if start < end:
        return weekday in schedule["days"] and start <= clock < end
    return (weekday in schedule["days"] and clock >= start) or ((weekday - 1) % 7 in schedule["days"] and clock < end)


def overlaps(values, actual):
    return not values or bool(set(values).intersection(actual))


def matching_recipient(rule, recipient, event):
    groups = {group.casefold() for group in [*recipient["groups"], recipient["team"]]}
    return (recipient["enabled"] and overlaps(recipient["severities"], [event["severity"]])
            and overlaps(recipient["host_group_ids"], event["host_group_ids"])
            and (recipient["id"] in rule["recipient_ids"] or any(group.casefold() in groups for group in rule["groups"])))


def matches_ports(rule, event):
    targets = rule.get("ports", [])
    ports = event.get("ports", [])
    return not targets or (len(ports) == 1 and any(
        (target["id"], target["host_id"]) == (ports[0]["id"], ports[0]["host_id"]) for target in targets))


def matching_rule(rule, event, age):
    return (rule["enabled"] and age >= rule["minimum_minutes"] and event["severity"] in rule["severities"]
            and overlaps(rule["categories"], [event["category"]]) and overlaps(rule["host_ids"], event["host_ids"])
            and matches_ports(rule, event) and overlaps(rule["host_group_ids"], event["host_group_ids"]) and overlaps(rule["problem_types"], [event["problem_type"]])
            and all(value in event["tags"].get(key, []) for key, value in rule["tags"].items()))


def scheduled_for(rule, recipient, config, event, now):
    local = datetime.fromtimestamp(now, ZoneInfo(config["timezone"]))
    quiet = config["quiet_hours"] and scheduled(config["quiet_hours"], local)
    return (scheduled(rule["schedule"], local) and scheduled(recipient["schedule"], local)
            and (not quiet or (rule["bypass_quiet"] and event["severity"] >= 4)))


def simulation_event(payload, now):
    return {**payload.model_dump(), "detected_at": now - payload.duration_minutes * 60, "event_id": "TEST",
            "host_ids": list(set(payload.host_ids) | {port.host_id for port in payload.ports}),
            "tags": {key: [value] for key, value in payload.tags.items()}}


def template_default(kind):
    label = {"critical": "Critical alert", "warning": "Infrastructure alert", "recovery": "Recovered",
             "escalation": "Escalated", "reminder": "Still active"}[kind]
    lines = ["Device: {{device_name}} ({{device_ip}})", "Category: {{device_category}}", "Interface: {{interface_name}}", "Problem: {{problem}}",
             "Severity: {{severity}}", "Detected: {{detected_at}}", "Status: {{device_status}}",
             "Elapsed: {{duration}}", "Zabbix event: {{event_id}}", "Dashboard: {{dashboard_link}}"]
    if kind == "recovery":
        lines.insert(7, "Recovered: {{recovered_at}}")
    return {"subject": f"[{label}] {{{{device_name}}}} · {{{{problem}}}}", "text": f"{label}\n\n" + "\n".join(lines),
            "html": f"<h2>{label}</h2>" + "".join(f"<p>{line}</p>" for line in lines)}


def render(template, event, now, zone, test=False):
    def stamp(value):
        return datetime.fromtimestamp(value, ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M:%S %Z") if value else "Unavailable"
    end = event.get("resolved_at") or now
    minutes = max(0, (end - event["detected_at"]) // 60)
    values = {"device_name": event["name"] or "Unknown device", "device_ip": event["ip"] or "Unavailable",
              "interface_name": ", ".join(port["name"] for port in event.get("ports", [])) or "Unavailable",
              "device_category": event["category"], "problem": event["problem"], "severity": SEVERITIES[event["severity"]],
              "detected_at": stamp(event["detected_at"]), "recovered_at": stamp(event.get("resolved_at")),
              "duration": f"{minutes // 60}h {minutes % 60}m", "event_id": event["event_id"],
              "device_status": "Recovered" if event.get("resolved_at") else "Problem active", "dashboard_link": settings.server.base_url}
    def replace(text, html=False):
        return PLACEHOLDER.sub(lambda match: escape(str(values[match[1]])) if html else str(values[match[1]]), text)
    subject = " ".join(replace(template["subject"]).split())[:480]
    return {"subject": f"[TEST] {subject}" if test else subject,
            "text": ("TEST ONLY — simulated notification.\n\n" if test else "") + replace(template["text"]),
            "html": (("<p><strong>TEST ONLY — simulated notification.</strong></p>" if test else "") + replace(template["html"], True))
                    if template["html"] else ""}
