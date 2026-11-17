from collections import defaultdict
from datetime import datetime, timezone
from functools import partial
from hashlib import sha256
from threading import Lock
from time import monotonic, time
from typing import Literal
import os
import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from zaddesh_core.api.deps import get_current_user, require_role
from zaddesh_core.api.v1.zabbix import HOST_FIELDS, HostID, hidden_host_ids, host_health, host_interfaces, read_agent_hosts, saved_api, visible
from zaddesh_core.database import get_db
from zaddesh_core.infrastructure_monitoring import (
    average, classify, fresh_item, health_score, identifier, interface_name, item_kind, numeric, objects, persistent_pressure,
    port_utilization, settings_for_monitoring,
)
from zaddesh_core.models.zabbix import ZabbixConnection
from zaddesh_core.zabbix_import import duration

router = APIRouter(prefix="/infrastructure-monitoring", tags=["Infrastructure monitoring"], dependencies=[Depends(get_current_user)])
RESOURCES = ("cpu", "memory")
ITEM_FIELDS = ["itemid", "hostid", "key_", "name", "units", "lastvalue", "lastclock", "delay", "value_type", "state"]
ITEM_PREFIXES = ["system.cpu", "cpu.", "hrProcessorLoad", "vm.memory", "memory.", "net.if.", "icmpping", "system.uptime",
                 "system.net.uptime", "sysUpTime", "system.hw", "sensor", "temp", "fan", "psu", "raid"]
UPLINK = re.compile(r"uplink|trunk|ten.?gig|\bte\d|\bxe-", re.I)
VIRTUAL_PORT = re.compile(r"^(?:lo(?:opback)?\d*|vlan\d*|docker\d*|veth\w+|null\d*)$", re.I)
TTL = 25
try:
    TTL = max(15, min(300, int(os.environ.get("ZADDESH_INFRA_REFRESH_SECONDS", TTL))))
except ValueError:
    pass

_lock = Lock()
_cache = None


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index:index + size]


def timestamp(clock):
    return datetime.fromtimestamp(clock, timezone.utc)


def port_id(name):
    return sha256(name.encode()).hexdigest()


def read_samples(api, items, now, seconds, *, trends=False):
    """Return lists of (clock, mean, min, max, count) indexed by Zabbix item ID."""
    series = defaultdict(list)
    for kind in ("0", "3"):
        ids = [item_id for item_id, item in items.items() if str(item.get("value_type")) == kind]
        for batch in chunks(ids, 80):
            options = {"itemids": batch, "time_from": now - seconds, "time_till": now, "limit": 8000}
            if trends:
                rows = api.rows("trend.get", {**options, "output": ["itemid", "clock", "value_min", "value_avg", "value_max", "num"]})
            else:
                rows = api.rows("history.get", {**options, "history": int(kind), "output": ["itemid", "clock", "value"],
                                                "sortfield": "clock", "sortorder": "DESC"})
            accepted = set(batch)
            for row in rows:
                item_id = str(row.get("itemid"))
                clock = numeric(row.get("clock"))
                value = numeric(row.get("value_avg" if trends else "value"))
                if item_id not in accepted or clock is None or not now - seconds <= clock <= now or value is None:
                    continue
                low = numeric(row.get("value_min")) if trends else value
                high = numeric(row.get("value_max")) if trends else value
                count = numeric(row.get("num")) if trends else 1
                if low is not None and high is not None and count is not None and count > 0:
                    series[item_id].append((int(clock), value, low, high, count))
    return series


def read_problems(api, host_ids):
    result = defaultdict(list)
    complete = True
    for batch in chunks(host_ids, 100):
        try:
            problems = api.rows("problem.get", {"hostids": batch, "source": 0, "object": 0, "recent": False,
                "output": ["eventid", "objectid", "name", "severity", "clock", "suppressed"], "limit": 1000})
            triggers = sorted({str(problem["objectid"]) for problem in problems if identifier(problem.get("objectid"))})
            assignments = {}
            for group in chunks(triggers, 100):
                for trigger in api.rows("trigger.get", {"triggerids": group, "output": ["triggerid"], "selectHosts": ["hostid"]}):
                    assignments[str(trigger.get("triggerid"))] = [str(host.get("hostid")) for host in objects(trigger.get("hosts"))]
            complete = complete and len(problems) < 1000 and all(trigger in assignments for trigger in triggers)
            for problem in problems:
                severity = numeric(problem.get("severity"))
                title = visible(problem.get("name"), api.token)
                if not title or severity is None or not 0 <= severity <= 5:
                    continue
                for host in assignments.get(str(problem.get("objectid")), ()):
                    if host in batch:
                        result[host].append({"id": str(problem.get("eventid")), "title": title, "severity": int(severity),
                                             "suppressed": str(problem.get("suppressed")) == "1"})
        except HTTPException:
            complete = False
    return result, complete


def device_items(items):
    resources = defaultdict(list)
    ports = defaultdict(dict)
    sensors = []
    for item in items:
        kind = item_kind(item)
        if kind in {"in", "out", "speed", "status", "adminstatus", "errors", "discards", "type"}:
            if name := interface_name(item):
                ports[name].setdefault(kind, item)
        elif kind == "sensor":
            sensors.append(item)
        elif kind:
            resources[kind].append(item)
    return resources, ports, sensors


def latest(item, now):
    return numeric(item.get("lastvalue")) if item and fresh_item(item, now) else None


def summarize(api, db, now):
    configuration = settings_for_monitoring()
    version = tuple(int(part) for part in api.version.split(".")[:2])
    groups_field = "selectHostGroups" if version >= (6, 2) else "selectGroups"
    params = {**HOST_FIELDS, groups_field: ["name"], "selectTags": ["tag", "value"], "selectParentTemplates": ["name"], "sortfield": "hostid"}
    if version >= (6, 2):
        params["output"] = [*HOST_FIELDS["output"], "active_available", "maintenance_status"]
        hosts = api.rows("host.get", params)
    else:
        params["output"] = [*HOST_FIELDS["output"], "maintenance_status"]
        hosts = read_agent_hosts(api, params)
    hidden = hidden_host_ids(db, str(api.url))
    hosts = [host for host in hosts if identifier(host.get("hostid")) and str(host["hostid"]) not in hidden]
    host_ids = [str(host["hostid"]) for host in hosts]
    source_items = defaultdict(list)
    for batch in chunks(host_ids, 10):
        for item in api.rows("item.get", {"hostids": batch, "output": ITEM_FIELDS, "filter": {"status": 0, "state": 0},
                                          "search": {"key_": ITEM_PREFIXES}, "startSearch": True, "searchByAny": True,
                                          "sortfield": "itemid", "limit": 2500}):
            if str(item.get("hostid")) in batch and identifier(item.get("itemid")):
                source_items[str(item["hostid"])].append(item)
    problems, problems_known = read_problems(api, host_ids)
    prepared = {}
    rolling_items = {}
    for host_id in host_ids:
        items, ports, sensors = device_items(source_items[host_id])
        curated = {key: min(values, key=lambda item: (not fresh_item(item, now), "[" in str(item["key_"]), len(str(item["key_"]))))
                   for key, values in items.items()}
        active = [(name, port, port_utilization(latest(port.get("in"), now), latest(port.get("out"), now), latest(port.get("speed"), now)))
                  for name, port in ports.items() if latest(port.get("status"), now) in (None, 1) and latest(port.get("adminstatus"), now) != 2
                  and latest(port.get("type"), now) not in {24, 53, 131, 135, 136} and not VIRTUAL_PORT.fullmatch(name)]
        candidates = [entry for entry in active if entry[2] is not None and
                      (configuration["network_policy"] == "busiest" or UPLINK.search(entry[0]) or (latest(entry[1].get("speed"), now) or 0) >= 10e9)]
        busiest = sorted(candidates, key=lambda entry: entry[2], reverse=True)[:3]
        for item in [*(curated[key] for key in (*RESOURCES, "ping") if key in curated),
                     *(port[direction] for _, port, _ in busiest for direction in ("in", "out") if direction in port)]:
            rolling_items[str(item["itemid"])] = item
        prepared[host_id] = curated, ports, sensors, busiest
    try:
        history = read_samples(api, rolling_items, now, 900)
    except HTTPException:
        history = {}

    devices = []
    details = {}
    for host in hosts:
        host_id = str(host["hostid"])
        items, ports, sensors, busiest = prepared[host_id]
        reading = partial(latest, now=now)
        def samples(item):
            return [(row[0], row[1]) for row in history.get(str(item.get("itemid")), ())] if item else []
        resources = {}
        pressure = False
        for key in RESOURCES:
            current = reading(items.get(key))
            if key == "memory" and current is None:
                free, total = reading(items.get("memory_available")), reading(items.get("memory_total"))
                if total and free is not None and 0 <= free <= total:
                    current = 100 * (1 - free / total)
            values = [(clock, value) for clock, value in samples(items.get(key)) if 0 <= value <= 100]
            if current is not None and 0 <= current <= 100:
                mean = average(values)
                resources[key] = {"current": current, "value": mean if mean is not None else current, "samples": len(values)}
                pressure = pressure or persistent_pressure(values)
        network = []
        for name, port, current in busiest:
            speed = reading(port.get("speed"))
            in_average, out_average = average(samples(port.get("in"))), average(samples(port.get("out")))
            rolled = port_utilization(in_average if in_average is not None else reading(port.get("in")),
                                      out_average if out_average is not None else reading(port.get("out")), speed)
            network.append((rolled if rolled is not None else current, current, name))
        if network:
            value, current, _ = max(network)
            resources["network"] = {"current": current, "value": value, "samples": sum(len(samples(port.get(direction)))
                                    for _, port, _ in busiest for direction in ("in", "out"))}

        pings = sorted(samples(items.get("ping")), reverse=True)[:3]
        fresh_clocks = [int(clock) for item in source_items[host_id] if fresh_item(item, now)
                        if (clock := numeric(item.get("lastclock"))) is not None]
        native, _ = host_health(host)
        maintenance = str(host.get("maintenance_status")) == "1"
        cadence = duration(items.get("ping", {}).get("delay")) or 60
        confirmed = (reading(items.get("ping")) == 0 and len(pings) == 3 and all(value == 0 for _, value in pings)
                     and pings[0][0] - pings[-1][0] <= cadence * 3)
        status = ("disabled" if native == "unmanaged" else "maintenance" if maintenance else "offline" if confirmed
                  else "online" if (reading(items.get("ping")) == 1 or native == "up") and fresh_clocks
                  else "warning" if native in {"warning", "down"} else "unknown")
        host_problems = problems[host_id]
        hardware = any(fresh_item(item, now) for item in sensors)
        components = {"interfaces": any(reading(port.get("status")) is not None for port in ports.values()),
                      "hardware": hardware, "performance": "cpu" in resources or "memory" in resources}
        failed_uplink = next((visible(f"{name} is administratively enabled but operationally down", api.token)
                              for name, port in ports.items() if UPLINK.search(name) and reading(port.get("adminstatus")) == 1
                              and reading(port.get("status")) in (2, 7)), None)
        score, modifiers = health_score(status, maintenance, bool(fresh_clocks), host_problems,
                                          [key for key, known in components.items() if known], pressure, failed_uplink,
                                          configuration["penalties"], problems_known)
        addresses = sorted(host_interfaces(host), key=lambda entry: str(entry.get("main")) != "1")
        address = next((value for entry in addresses if (value := entry.get("ip" if entry.get("useip") == "1" else "dns"))), "")
        severity = max((problem["severity"] for problem in host_problems if not problem["suppressed"]), default=0)
        device = {"id": f"zabbix-{host_id}", "host_id": host_id, "name": visible(host.get("name") or host.get("host"), api.token),
                  "ip": visible(address, api.token), "category": classify(host), "status": status, "severity": severity,
                  "resources": resources if status not in {"offline", "disabled"} else {}, "health": score, "penalties": modifiers,
                  "factors": components, "problem_count": sum(not problem["suppressed"] for problem in host_problems),
                  "critical_count": sum(not problem["suppressed"] and problem["severity"] >= 4 for problem in host_problems),
                  "problems_known": problems_known, "last_seen": timestamp(max(fresh_clocks)) if fresh_clocks else None}
        devices.append(device)
        details[host_id] = device, items, ports, sensors, host_problems, busiest
    return {"devices": devices, "config": configuration, "at": timestamp(now), "refresh_seconds": TTL}, details


def snapshot(db):
    global _cache
    connection = db.get(ZabbixConnection, 1)
    if connection is None:
        return {"devices": [], "config": settings_for_monitoring(), "at": None, "refresh_seconds": TTL}, {}
    key = (connection.api_url, connection.encrypted_token)
    with _lock:
        if _cache and _cache[0] == key and monotonic() - _cache[1] < TTL:
            return _cache[2]
        with saved_api(db) as api:
            result = summarize(api, db, int(time()))
        _cache = key, monotonic(), result
        return result


@router.get("")
def monitoring_summary(db: Session = Depends(get_db)):
    return snapshot(db)[0]


@router.get("/{host_id}", dependencies=[Depends(require_role(["admin"]))])
def monitoring_detail(host_id: HostID, hours: Literal["1", "6", "24", "168", "720"] = "1",
                      interface: str | None = Query(None, pattern=r"^[0-9a-f]{64}$"), db: Session = Depends(get_db)):
    _, records = snapshot(db)
    if host_id not in records:
        raise HTTPException(404, "Device not found in Zabbix monitoring")
    device, items, ports, sensors, problems, busiest = records[host_id]
    port_name = next((name for name in ports if port_id(name) == interface), None) if interface else None
    if interface is not None and port_name is None:
        raise HTTPException(404, "Interface not found in Zabbix monitoring")
    now = int(time())
    selections = {key: items[key] for key in (*RESOURCES, "memory_available", "memory_total") if key in items}
    selected_ports = [(port_name, ports[port_name])] if port_name is not None else [(name, port) for name, port, _ in busiest[:1]]
    if interface is not None:
        selections = {}
    for name, port in selected_ports:
        selections.update({f"{name} {key}": port[key] for key in ("in", "out", "errors", "discards") if key in port})
    seconds = int(hours) * 3600
    trends = int(hours) >= 168
    with saved_api(db) as api:
        selected = {str(item["itemid"]): item for item in selections.values()}
        data = read_samples(api, selected, now, seconds, trends=trends)
        series = []
        for label, item in selections.items():
            rows = sorted(data.get(str(item["itemid"]), ()))
            if not rows:
                continue
            step = max(1, (len(rows) + 719) // 720)
            interval = 3600 if trends else duration(item.get("delay"))
            series.append({"name": visible(label, api.token), "unit": visible(item.get("units"), api.token),
                           "current": latest(item, now), "min": min(row[2] for row in rows), "max": max(row[3] for row in rows),
                           "interval_seconds": interval * step if interval else None,
                           "avg": sum(row[1] * row[4] for row in rows) / sum(row[4] for row in rows),
                           "points": [{"timestamp": timestamp(clock), "value": value} for clock, value, *_ in rows[::step]]})
        if interface is not None:
            return {"series": series, "trends": trends}
        port_details = []
        for name, port in ports.items():
            values = {key: latest(port.get(key), now) for key in ("in", "out", "speed", "status", "adminstatus", "errors", "discards")}
            port_details.append({"id": port_id(name), "name": visible(name, api.token), **values,
                                 "utilization": port_utilization(values["in"], values["out"], values["speed"]),
                                 "rx_utilization": port_utilization(values["in"], None, values["speed"]),
                                 "tx_utilization": port_utilization(None, values["out"], values["speed"]),
                                 "uplink": bool(UPLINK.search(name)) or bool((values["speed"] or 0) >= 10e9)})
        hardware = [{"name": visible(item.get("name"), api.token), "value": visible(str(item.get("lastvalue", "")), api.token),
                     "unit": visible(item.get("units"), api.token)} for item in sensors if fresh_item(item, now)]
        identity = {key: visible(str(items.get(key, {}).get("lastvalue", "")), api.token) for key in ("vendor", "model")}
    return {"device": device, "series": series, "trends": trends, "interfaces": port_details, "sensors": hardware, "problems": problems,
            "uptime": latest(items.get("uptime"), now), "memory_available": latest(items.get("memory_available"), now),
            "memory_total": latest(items.get("memory_total"), now), **identity}
