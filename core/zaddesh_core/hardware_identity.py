"""Hardware identity from reported inventory and standard SNMP identity objects."""
import re

# Cisco's published CISCO-ENTITY-VENDORTYPE-OID-MIB, cevChassisN3kC3064PQ10GX.
# https://github.com/cisco/cisco-mibs/blob/main/v2/CISCO-ENTITY-VENDORTYPE-OID-MIB.my
# Catalyst identities are from Cisco's CISCO-PRODUCTS-MIB.my in the same repository.
# Stack OID 516 identifies the family, not the individual member chassis SKU.
PRODUCTS = {
    '1.3.6.1.4.1.9.12.3.1.3.1163': ('Cisco', 'N3K-C3064PQ-10GX'),
    '1.3.6.1.4.1.9.1.516': ('Cisco', 'Catalyst 37xx stack'),
    '1.3.6.1.4.1.9.1.659': ('Cisco', 'Catalyst 4948-10GE'),
    '1.3.6.1.4.1.9.1.794': ('Cisco', 'Catalyst 3560E-48TD'),
    '1.3.6.1.4.1.9.1.1226': ('Cisco', 'Catalyst 3560X-24'),
    '1.3.6.1.4.1.9.1.1227': ('Cisco', 'Catalyst 3560X-48'),
    '1.3.6.1.4.1.9.1.1229': ('Cisco', 'Catalyst 3560X-48P'),
}
ENTERPRISES = {'9': 'Cisco', '11': 'HP', '674': 'Dell', '2636': 'Juniper', '14988': 'MikroTik',
               '41112': 'Ubiquiti', '2011': 'Huawei', '25506': 'H3C', '12356': 'Fortinet', '318': 'APC'}
PLACEHOLDERS = {'', '0', 'unknown', 'generic', 'none', 'n/a', 'not available', 'not reported',
                'to be filled by o.e.m.', 'system product name', 'default string'}
ENTITY = '1.3.6.1.2.1.47.1.1.1.1.'


def clean(value):
    text = str(value or '').strip()
    return '' if text.casefold() in PLACEHOLDERS else re.sub(r'[\x00-\x1f\x7f]', ' ', text)[:128]


def identity(*, vendor='', model='', description='', object_id=''):
    vendor, model = clean(vendor), clean(model)
    oid = str(object_id).strip().lstrip('.')
    product = PRODUCTS.get(oid)
    if product:
        vendor, model = vendor or product[0], model or product[1]
    if not vendor:
        enterprise = re.match(r'^1\.3\.6\.1\.4\.1\.(\d+)(?:\.|$)', oid)
        vendor = ENTERPRISES.get(enterprise[1], '') if enterprise else ''
    if not vendor:
        for pattern, name in [(r'\bcisco\b', 'Cisco'), (r'\bdell\b', 'Dell'),
                              (r'\bhewlett.packard\b|\bproliant\b|\bHPE\b', 'HPE'), (r'\bjuniper\b', 'Juniper'),
                              (r'\bmikrotik\b|\brouteros\b', 'MikroTik'), (r'\bubiquiti\b', 'Ubiquiti'),
                              (r'\bfortinet\b|\bfortigate\b', 'Fortinet'), (r'\bhuawei\b', 'Huawei')]:
            if re.search(pattern, description, re.I):
                vendor = name
                break
    if not model:
        match = re.search(r'\b(?:N[23579]K-C[A-Z0-9-]+|WS-C[A-Z0-9-]+|C9\d{3}[A-Z0-9-]*|PowerEdge\s+[A-Z0-9-]+|ProLiant\s+[A-Z0-9]+(?:\s+Gen\d+)?)\b', description, re.I)
        if match:
            model = match[0]
    return {'vendor': vendor[:64] or None, 'model': model[:64] or None}


def snmp_identity(values):
    chassis = [oid[len(ENTITY + '5.'): ] for oid, value in values.items()
               if oid.startswith(ENTITY + '5.') and str(value) == '3']
    models = [oid[len(ENTITY + '13.'): ] for oid in values if oid.startswith(ENTITY + '13.') and clean(values[oid])]
    candidates = chassis or (models if len(models) == 1 else [])
    index = next((index for index in candidates if clean(values.get(ENTITY + '13.' + index))
                  or clean(values.get(ENTITY + '12.' + index))), None)
    return identity(vendor=values.get(ENTITY + '12.' + index, '') if index else '',
                    model=values.get(ENTITY + '13.' + index, '') if index else '',
                    description=values.get('1.3.6.1.2.1.1.1.0', ''), object_id=values.get('1.3.6.1.2.1.1.2.0', ''))


def apply_identity(device, found):
    for field in ('vendor', 'model'):
        if found.get(field) and not clean(getattr(device, field)):
            setattr(device, field, found[field])
