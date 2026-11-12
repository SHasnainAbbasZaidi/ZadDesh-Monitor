from __future__ import annotations

import ipaddress
import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from zaddesh_core.schemas.check import InitialSnmpCheck
from zaddesh_core.security import encrypt_secret

AUTH_PROTOCOLS = {"0": "md5", "1": "sha1", "2": "sha224", "3": "sha256", "4": "sha384", "5": "sha512"}
PRIV_PROTOCOLS = {"0": "des", "1": "aes128", "2": "aes192", "3": "aes256"}
SECURITY_LEVELS = {"0": "noAuthNoPriv", "1": "authNoPriv", "2": "authPriv"}
OID = re.compile(r"\.?[0-9]+(?:\.[0-9]+){1,63}")
DNS_NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*")


def canonical_ip(value: Any) -> str | None:
    try:
        address = ipaddress.ip_address(value)
        return str(getattr(address, "ipv4_mapped", None) or address)
    except ValueError:
        return None


def target_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    clean = value.strip().rstrip(".").lower()
    return canonical_ip(clean) or (clean if len(clean) <= 253 and DNS_NAME.fullmatch(clean) else None)


def interface_target(interface: dict[str, Any]) -> str | None:
    if str(interface.get("useip")) == "1":
        return canonical_ip(interface.get("ip"))
    return target_name(interface.get("dns")) if str(interface.get("useip")) == "0" else None


def duration(value: Any) -> int | None:
    matched = re.fullmatch(r"(\d{1,6})([smhd]?)", str(value))
    if not matched:
        return None
    return int(matched[1]) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[matched[2]]


def literal_secret(value: Any) -> str | None:
    if isinstance(value, str) and value and not re.search(r"\{[^}]*\}|^\*{3,}$|^\[redacted\]$", value, re.I):
        return value
    return None


def resolve_interface_credentials(interfaces: list[dict[str, Any]], macros: Any) -> None:
    if not isinstance(macros, list):
        return
    literals = {item["macro"]: item["value"] for item in macros if isinstance(item, dict)
                and str(item.get("type")) == "0" and isinstance(item.get("macro"), str)
                and isinstance(item.get("value"), str)}
    for interface in interfaces:
        details = interface.get("details")
        if not isinstance(details, dict):
            continue
        for field in ("community", "securityname", "authpassphrase", "privpassphrase"):
            value = details.get(field)
            if isinstance(value, str) and value in literals:
                details[field] = literals[value]


class UnsupportedItem(ValueError):
    pass


@dataclass
class ImportedCheck:
    check_type: str
    target: str
    params: dict[str, Any]
    interval: int
    timeout: int
    enabled: bool
    interface_id: str = ""


def snmp_interface(interface: dict[str, Any]) -> tuple[dict[str, Any], str]:
    details = interface.get("details")
    if not isinstance(details, dict):
        raise UnsupportedItem("SNMP interface settings are unavailable")
    version = {"1": "1", "2": "2c", "3": "3"}.get(str(details.get("version")))
    port = duration(interface.get("port"))
    if not version or not port or not 1 <= port <= 65535 or not str(interface.get("port")).isdecimal():
        raise UnsupportedItem("SNMP interface version or port is unsupported or uses a macro")
    params: dict[str, Any] = {"version": version, "port": port}
    if version == "3":
        level = SECURITY_LEVELS.get(str(details.get("securitylevel", "0")))
        if not level:
            raise UnsupportedItem("SNMPv3 security level is unsupported")
        context = details.get("contextname") or ""
        if not isinstance(context, str) or (context and literal_secret(context) is None):
            raise UnsupportedItem("SNMPv3 context uses an unresolved macro")
        params.update(security_level=level, context_name=context)
        credentials = {"username": literal_secret(details.get("securityname"))}
        if level != "noAuthNoPriv":
            protocol = AUTH_PROTOCOLS.get(str(details.get("authprotocol", "0")))
            if not protocol:
                raise UnsupportedItem("SNMPv3 authentication protocol is unsupported")
            params["auth_protocol"] = protocol
            credentials["auth_password"] = literal_secret(details.get("authpassphrase"))
        if level == "authPriv":
            privacy = PRIV_PROTOCOLS.get(str(details.get("privprotocol", "0")))
            if not privacy:
                raise UnsupportedItem("SNMPv3 privacy protocol is unsupported")
            params["privacy_protocol"] = privacy
            credentials["priv_password"] = literal_secret(details.get("privpassphrase"))
    else:
        credentials = {"community": literal_secret(details.get("community"))}
    if any(value is None for value in credentials.values()):
        raise UnsupportedItem("SNMP credentials are hidden or use unresolved macros")
    try:
        parsed = InitialSnmpCheck.model_validate({"params": params, "credentials": credentials})
        ciphertext = encrypt_secret(json.dumps(credentials))
    except ValidationError:
        raise UnsupportedItem("SNMP interface settings or credentials are unsupported") from None
    except RuntimeError:
        raise UnsupportedItem("SNMP credential storage requires the core encryption key") from None
    return parsed.params.model_dump(exclude_none=True), ciphertext


def prepare_checks(interfaces: list[dict[str, Any]], items: list[dict[str, Any]], primary: str) -> tuple[
    list[tuple[str, ImportedCheck]], dict[str, tuple[dict[str, Any], str]], Counter[str],
]:
    by_id = {str(interface.get("interfaceid")): interface for interface in interfaces if interface.get("interfaceid")}
    snmp: dict[str, tuple[dict[str, Any], str]] = {}
    interface_errors: dict[str, str] = {}
    skipped: Counter[str] = Counter()
    checks = []
    seen_icmp: set[tuple[str, int, int, bool]] = set()
    for item in items:
        item_type = str(item.get("type"))
        key = str(item.get("key_"))
        if item_type != "20" and not (item_type == "3" and key in {"icmpping", "icmppingloss", "icmppingsec"}):
            skipped["Zabbix-only item types or parameters"] += 1
            continue
        interval = duration(item.get("delay"))
        timeout = duration(item.get("timeout"))
        if interval is None or not 5 <= interval <= 86400 or (timeout is not None and timeout > 120):
            skipped["Custom or unsupported polling schedule"] += 1
            continue
        if timeout is None and item.get("timeout") not in (None, ""):
            skipped["Unresolved item timeout"] += 1
            continue
        timeout = timeout or 5
        enabled = str(item.get("status", "0")) == "0"
        interface_id = str(item.get("interfaceid", "0"))
        interface = by_id.get(interface_id)
        target = interface_target(interface) if interface is not None else primary if interface_id == "0" else None
        if not target:
            skipped["Item interface has no usable target"] += 1
            continue
        name = item.get("name") or key
        if item_type == "3":
            identity = (target, interval, timeout, enabled)
            if identity not in seen_icmp:
                checks.append((str(name), ImportedCheck("icmp", target, {}, interval, timeout, enabled)))
                seen_icmp.add(identity)
            continue
        oid = str(item.get("snmp_oid", ""))
        if oid.startswith("get[") and oid.endswith("]"):
            oid = oid[4:-1]
        if len(oid) > 123 or not OID.fullmatch(oid):
            skipped["SNMP item requires a numeric GET OID"] += 1
            continue
        if interface is None or str(interface.get("type")) != "2":
            skipped["SNMP item has no usable SNMP interface"] += 1
            continue
        if interface_id not in snmp and interface_id not in interface_errors:
            try:
                snmp[interface_id] = snmp_interface(interface)
            except UnsupportedItem as error:
                interface_errors[interface_id] = str(error)
        if interface_id in interface_errors:
            skipped[interface_errors[interface_id]] += 1
            continue
        params = {**snmp[interface_id][0], "oid": oid.lstrip(".")}
        checks.append((str(name), ImportedCheck("snmp", target, params, interval, timeout, enabled, interface_id)))
    return checks, snmp, skipped
