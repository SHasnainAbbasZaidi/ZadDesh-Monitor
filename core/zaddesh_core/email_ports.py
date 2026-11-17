"""Port identities shared by the Zabbix rule picker and event routing."""

from hashlib import sha256
import re

from zaddesh_core.infrastructure_monitoring import identifier, interface_name

PORT_ITEM = re.compile(r"^net\.if\.(?:in|out|speed|status|type|adminstatus)\[", re.I)
PORT_FIELDS = ["hostid", "key_", "name"]


def port_target(item):
    host_id = str(item.get("hostid"))
    key = str(item.get("key_", ""))
    if not identifier(host_id) or not PORT_ITEM.match(key):
        return None
    name = interface_name(item).strip()
    if not name or "{" in name or "}" in name or any(ord(character) < 32 for character in name):
        return None
    # Zabbix interface item names can append an alias in parentheses. Keep the port identity stable when it changes.
    if re.match(r"^Interface .+?:\s", str(item.get("name", "")), re.I):
        name = re.sub(r"\s*\([^()]*\)$", "", name).strip()
    elif re.fullmatch(r"if[A-Za-z]+\.\d+", key.partition("[")[2].partition("]")[0].split(",", 1)[0].strip('"')):
        name = f"ifIndex {name}"
    if not name or len(name) > 200:
        return None
    return {"id": sha256(f"{host_id}\0{name.casefold()}".encode()).hexdigest(), "host_id": host_id, "name": name}


def monitored_ports(api, host_id):
    items = api.rows("item.get", {"hostids": [host_id], "output": PORT_FIELDS,
                                 "filter": {"status": 0}, "search": {"key_": "net.if."},
                                 "startSearch": True, "limit": 5000})
    ports = {port["id"]: port for item in items if (port := port_target(item)) and port["host_id"] == host_id}
    return sorted(ports.values(), key=lambda port: (port["name"].casefold(), port["id"]))
