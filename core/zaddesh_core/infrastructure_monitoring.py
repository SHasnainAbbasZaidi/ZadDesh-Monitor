"""Read-only aggregation for the Overview infrastructure monitoring section."""

from math import isfinite
from statistics import fmean
import json
import os
import re

from zaddesh_core.zabbix_import import duration

CATEGORIES = ("Network", "Servers", "Routers", "Firewalls", "CCTV", "Other Devices")
DEFAULT_WEIGHTS = {"cpu": 40, "memory": 30, "network": 30}
PENALTIES = {"hardware": 35, "uplink": 25, "errors": 15, "pressure": 10, "warning": 5}
CLASSIFIERS = (
    ("Firewalls", r"\b(firewall\w*|fortigate|fortinet|pfsense|opnsense|firepower|sonicwall|pan[- ]?os)\b"),
    ("CCTV", r"\b(camera\w*|cctv|surveillance|nvr|dvr|hikvision|dahua)\b"),
    ("Routers", r"\b(router\w*|routeros|routing|vyos|csr1000v|asr\d*)\b"),
    ("Network", r"\b(switch\w*|wireless|access points?|unifi|ubiquiti|nexus|catalyst|procurve|aruba|bridges?)\b"),
    ("Other Devices", r"\b(ups|printer\w*|nas|storage|san|synology|qnap)\b"),
    ("Servers", r"\b(server\w*|windows|linux|hyper-?v|esxi|vmware|proxmox|virtuali[sz]ation)\b"),
)


def settings_for_monitoring():
    def configured(name, defaults):
        try:
            values = json.loads(os.environ.get(name, "{}"))
        except ValueError:
            return defaults
        if not isinstance(values, dict):
            return defaults
        return {key: values[key] if type(values.get(key)) in (int, float) and 0 <= values[key] <= 100 else value
                for key, value in defaults.items()}

    return {
        "weights": {category: configured(f"ZADDESH_INFRA_WEIGHTS_{category.upper().replace(' ', '_')}", DEFAULT_WEIGHTS)
                    for category in CATEGORIES},
        "penalties": configured("ZADDESH_INFRA_PENALTIES", PENALTIES),
        "network_policy": ("uplinks" if os.environ.get("ZADDESH_INFRA_NETWORK_POLICY") == "uplinks" else "busiest"),
    }


def objects(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def classify(host):
    tags = objects(host.get("tags")) + objects(host.get("inheritedTags"))
    category_tags = [str(tag.get("value", "")) for tag in tags if str(tag.get("tag", "")).casefold()
                     in {"role", "device", "device_type", "category"}]
    groups = objects(host.get("hostgroups", host.get("groups")))
    templates = objects(host.get("parentTemplates"))
    for values in (category_tags, [str(row.get("name", "")) for row in groups + templates],
                   [str(host.get("name", "")), str(host.get("host", ""))]):
        value = " ".join(values).replace("_", " ").casefold()
        for category, expression in CLASSIFIERS:
            if re.search(expression, value):
                return category
    return "Other Devices"


def numeric(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if isfinite(number) else None


def identifier(value):
    return re.fullmatch(r"[0-9]{1,20}", str(value)) is not None


def item_kind(item):
    key = str(item.get("key_", "")).casefold()
    units = str(item.get("units", "")).casefold()
    if (re.match(r"^(system\.cpu\.(util|usage)|hrprocessorload|cpu\.(util|usage))", key)
            and not re.search(r"[,\[.](idle|user|system|nice|iowait|interrupt|steal)([,\].]|$)", key)):
        return "cpu" if units == "%" else None
    if key in {"vm.memory.utilization", "vm.memory.size[pused]", "memory.utilization"} and units == "%":
        return "memory"
    if key in {"vm.memory.size[available]", "vm.memory.size[total]"} and units in {"b", "bytes"}:
        return "memory_available" if "available" in key else "memory_total"
    if key == "icmpping" or key.startswith("icmpping["):
        return "ping"
    if key in {"system.uptime", "system.net.uptime", "sysuptime"}:
        return "uptime"
    if key in {"system.hw.model", "system.hw.vendor", "system.hw.manufacturer"}:
        return "model" if key.endswith("model") else "vendor"
    port = re.match(r"^net\.if\.(in|out|speed|status|type|adminstatus)\[", key)
    if port:
        field = port[1]
        if field in {"in", "out"}:
            if any(term in key for term in ("errors", "discards", "dropped")):
                return "errors" if "errors" in key else "discards"
            return field if units in {"bps", "b/s", "bit/s"} and "pps" not in key else None
        return field if field != "speed" or units in {"bps", "b/s", "bit/s"} else None
    if re.search(r"temp|fan|power.?supply|raid", key):
        return "sensor"
    return None


def fresh_item(item, now):
    clock = numeric(item.get("lastclock")) or 0
    cadence = duration(item.get("delay")) or 0
    return 0 <= now - clock <= min(1800, max(180, cadence * 3))


def interface_name(item):
    name = str(item.get("name", ""))
    # Discovered SNMP item names preserve the port name across different OID indices.
    named = re.match(r"^Interface (.+?):\s", name, re.I)
    if named:
        return named[1]
    key = str(item.get("key_", ""))
    argument = key.partition("[")[2].partition("]")[0].split(",", 1)[0].strip('"')
    return argument.split(".")[-1] if re.fullmatch(r"if[A-Za-z]+\.\d+", argument) else argument


def port_utilization(incoming, outgoing, speed):
    rates = [value for value in (incoming, outgoing) if value is not None and value >= 0]
    return min(100, max(rates) / speed * 100) if rates and speed and speed > 0 else None


def average(samples):
    return fmean(value for _, value in samples) if samples else None


def persistent_pressure(samples):
    return len(samples) >= 3 and max(clock for clock, _ in samples) - min(clock for clock, _ in samples) >= 300 and all(
        value >= 90 for _, value in samples)


def problem_factor(title, severity):
    title = title.casefold()
    if re.search(r"unavailable|unreachable|offline|no snmp|not available", title) and not re.search(r"interface|port|uplink|fan", title):
        return "connectivity"
    if re.search(r"raid|power.supply|fan|temperature|hardware fault|disk failure", title):
        return "hardware" if severity >= 3 else "warning"
    if re.search(r"errors?|discards?|flapp", title):
        return "errors" if severity >= 2 else "warning"
    if re.search(r"link down|interface.*down|port.*down", title):
        return "uplink" if re.search(r"uplink|trunk|tengig|ten.?gig|\bte\d|\bxe-", title) else "warning"
    if re.search(r"cpu|memory|swap", title):
        return "performance"
    return "warning" if severity >= 2 else None


def health_score(status, maintenance, fresh, problems, observed, pressure, failed_uplink, penalties, problems_known):
    findings = {}
    for problem in problems:
        if problem["suppressed"]:
            continue
        factor = problem_factor(problem["title"], problem["severity"])
        if factor in penalties:
            findings.setdefault(factor, problem["title"])
    if pressure:
        findings["pressure"] = "CPU or memory stayed at or above 90% across at least five minutes"
    if failed_uplink:
        findings.setdefault("uplink", failed_uplink)
    modifiers = [{"factor": factor, "reason": reason, "points": penalties[factor]} for factor, reason in findings.items()]
    score = None
    if not maintenance:
        if status == "offline":
            score = 0
        elif status == "online" and fresh and problems_known and observed:
            score = max(0, 100 - sum(item["points"] for item in modifiers))
    return score, modifiers
