"""Deterministic correlation of SNMP forwarding, ARP and interface observations."""
import re


def mac_address(value):
    value = str(value).strip().lower()
    if not re.fullmatch(r'(?:[0-9a-f]{12}|(?:[0-9a-f]{2}:){5}[0-9a-f]{2}|(?:[0-9a-f]{2}-){5}[0-9a-f]{2}|(?:[0-9a-f]{4}\.){2}[0-9a-f]{4})', value):
        return None
    raw = re.sub(r'[^0-9a-f]', '', value)
    return ':'.join(raw[index:index+2] for index in range(0, 12, 2)) if len(raw) == 12 else None


def endpoints(values, interfaces, now):
    bridge = {key.removeprefix('1.3.6.1.2.1.17.1.4.1.2.'): str(value) for key, value in values.items()
              if key.startswith('1.3.6.1.2.1.17.1.4.1.2.')}
    addresses = {}
    for oid, value in values.items():
        if oid.startswith('1.3.6.1.2.1.4.22.1.2.'):
            mac = mac_address(value)
            if mac:
                addresses.setdefault(mac, set()).add('.'.join(oid.split('.')[-4:]))
    result = []
    for prefix, status_prefix, vlan in [('1.3.6.1.2.1.17.4.3.1.2.', '1.3.6.1.2.1.17.4.3.1.3.', False),
                                         ('1.3.6.1.2.1.17.7.1.2.2.1.2.', '1.3.6.1.2.1.17.7.1.2.2.1.3.', True)]:
        for oid, port in values.items():
            if not oid.startswith(prefix) or str(port) == '0':
                continue
            suffix = oid[len(prefix):]
            status = values.get(status_prefix+suffix)
            if status is not None and str(status) != '3':
                continue  # Only learned forwarding entries, not self/static/invalid entries.
            octets = suffix.split('.')[-6:]
            if len(octets) != 6 or not all(part.isdigit() and 0 <= int(part) <= 255 for part in octets):
                continue
            mac = ':'.join(f'{int(part):02x}' for part in octets)
            if int(octets[0]) & 1 or mac == '00:00:00:00:00:00':
                continue
            index = bridge.get(str(port))
            interface = interfaces.get(index, {})
            result.append({'mac': mac, 'ips': sorted(addresses.get(mac, [])), 'if_index': index,
                'port': interface.get('name') or f'Bridge port {port}', 'bridge_port': str(port),
                'fdb_id': suffix.split('.')[0] if vlan else None, 'username': None,
                'observed_at': now.isoformat()+'Z', 'evidence': 'Q-BRIDGE-MIB' if vlan else 'BRIDGE-MIB'})
    learned = {entry['mac'] for entry in result}
    for oid, raw in values.items():
        prefix = '1.3.6.1.2.1.4.22.1.2.'
        if not oid.startswith(prefix):
            continue
        mac = mac_address(raw)
        if not mac or mac in learned:
            continue
        suffix = oid[len(prefix):].split('.')
        if len(suffix) != 5:
            continue
        index = suffix[0]
        result.append({'mac': mac, 'ips': sorted(addresses.get(mac, [])), 'if_index': index,
            'port': interfaces.get(index, {}).get('name') or f'Interface {index}', 'bridge_port': index,
            'fdb_id': None, 'username': None, 'observed_at': now.isoformat()+'Z', 'evidence': 'IP-MIB ARP'})
    return result


def latest_nodes(rows):
    result = {}
    for node in rows:
        if node.device_id is None:
            continue
        current = result.get(node.device_id)
        at = node.metadata_json.get('observed_at') or max((port.get('at', '') for port in node.metadata_json.get('interfaces', {}).values()), default='')
        if current is None or at > current[0]:
            result[node.device_id] = (at, node)
    return {identifier: node for identifier, (_, node) in result.items()}
