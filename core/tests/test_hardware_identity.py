from zaddesh_core.hardware_identity import ENTITY, identity, snmp_identity, apply_identity
from zaddesh_core.models import Device
import pytest
from pydantic import ValidationError
from zaddesh_core.config import settings, SnmpIdentityConfig


def test_official_cisco_product_oid_resolves_exact_hardware():
    assert identity(object_id='.1.3.6.1.4.1.9.12.3.1.3.1163') == {'vendor': 'Cisco', 'model': 'N3K-C3064PQ-10GX'}


@pytest.mark.parametrize('number,model', [(516, 'Catalyst 37xx stack'), (659, 'Catalyst 4948-10GE'),
    (794, 'Catalyst 3560E-48TD'), (1226, 'Catalyst 3560X-24'), (1227, 'Catalyst 3560X-48'), (1229, 'Catalyst 3560X-48P')])
def test_official_catalyst_product_identities(number, model):
    assert identity(object_id=f'.1.3.6.1.4.1.9.1.{number}') == {'vendor': 'Cisco', 'model': model}


def test_entity_mib_uses_chassis_not_interface_module():
    data = {ENTITY+'5.1': '9', ENTITY+'12.1': 'Module maker', ENTITY+'13.1': 'Line card',
            ENTITY+'5.2': '3', ENTITY+'12.2': 'Dell', ENTITY+'13.2': 'PowerEdge R740'}
    assert snmp_identity(data) == {'vendor': 'Dell', 'model': 'PowerEdge R740'}
    assert snmp_identity({ENTITY+'5.2': '3', ENTITY+'12.2': 'Dell'}) == {'vendor': 'Dell', 'model': None}


def test_os_and_agent_identity_do_not_pretend_to_be_hardware():
    assert identity(description='Microsoft Windows Server 2022', object_id='1.3.6.1.4.1.8072.3.2.10') == {'vendor': None, 'model': None}
    assert identity(model='To be filled by O.E.M.')['model'] is None
    assert snmp_identity({ENTITY+'13.1': 'Line card', ENTITY+'13.2': 'Power supply'})['model'] is None


def test_discovery_fills_generic_but_preserves_user_hardware():
    device = Device(name='Test', ip='192.0.2.1', vendor='Generic', model=None)
    apply_identity(device, identity(vendor='Dell', model='PowerEdge R740'))
    assert (device.vendor, device.model) == ('Dell', 'PowerEdge R740')
    apply_identity(device, identity(vendor='Cisco', model='Another model'))
    assert device.vendor == 'Dell'


@pytest.mark.parametrize('vendor,model', [('Juniper Networks', 'EX-any'), ('Ubiquiti', 'USW-any'),
    ('Aruba', 'CX-any'), ('Unlisted Manufacturer', 'Future-9000')])
def test_standard_entity_identity_needs_no_vendor_catalog(vendor, model):
    values = {'.'+ENTITY+'5.97': 'chassis(3)', '.'+ENTITY+'12.97': vendor, '.'+ENTITY+'13.97': model,
              '.'+ENTITY+'5.2': 'module(9)', '.'+ENTITY+'13.2': 'Unrelated line card'}
    assert snmp_identity(values) == {'vendor': vendor, 'model': model}


def test_modules_and_heterogeneous_stacks_do_not_invent_chassis_model():
    assert snmp_identity({ENTITY+'5.1': '9', ENTITY+'13.1': 'Only line card'})['model'] is None
    values = {ENTITY+'5.1': '3', ENTITY+'12.1': 'New Manufacturer', ENTITY+'13.1': 'Model-A',
              ENTITY+'5.2': '3', ENTITY+'12.2': 'New Manufacturer', ENTITY+'13.2': 'Model-B'}
    assert snmp_identity(values) == {'vendor': 'New Manufacturer', 'model': None}
    assert snmp_identity({ENTITY+'5.99': 'stack(11)', ENTITY+'12.99': 'New Manufacturer', ENTITY+'13.99': 'Stack-Z'})['model'] == 'Stack-Z'


def test_new_enterprise_and_product_are_configurable_without_code_changes(monkeypatch):
    config = SnmpIdentityConfig(products={'1.3.6.1.4.1.99999.7': {'vendor': 'Future Manufacturer', 'model': 'Model-7'}},
                                enterprises={'99999': 'Future Manufacturer'})
    monkeypatch.setattr(settings, 'snmp_identity', config)
    assert identity(object_id='1.3.6.1.4.1.99999.7') == {'vendor': 'Future Manufacturer', 'model': 'Model-7'}
    assert identity(object_id='1.3.6.1.4.1.99999.8') == {'vendor': 'Future Manufacturer', 'model': None}
    assert identity(vendor='Reported OEM', model='Reported model', object_id='1.3.6.1.4.1.99999.7')['vendor'] == 'Reported OEM'
    with pytest.raises(ValidationError):
        SnmpIdentityConfig(products={'not-an-oid': {'vendor': 'Maker', 'model': 'Model'}})


def test_zabbix_custom_item_names_resolve_by_standard_oids():
    from zaddesh_core.api.v1.zabbix import hardware_items
    class Remote:
        url = 'https://192.0.2.80/api_jsonrpc.php'
        token = 'browser-independent-test-fixture'
        def rows(self, method, params):
            assert method == 'item.get' and 'snmp_oid' in params['search']
            return [{'hostid': '17', 'key_': 'site.custom.'+index, 'snmp_oid': oid, 'lastvalue': value}
                    for index, oid, value in [('1', ENTITY+'5.1', '9'), ('2', ENTITY+'13.1', 'Module-Z'),
                        ('3', ENTITY+'5.8', '3'), ('4', ENTITY+'12.8', 'New Vendor'), ('5', 'get['+ENTITY+'13.8]', 'Chassis-X')]]
    assert hardware_items(Remote(), [{'hostid': '17'}])['17'] == {'vendor': 'New Vendor', 'model': 'Chassis-X'}
