from zaddesh_core.hardware_identity import ENTITY, identity, snmp_identity, apply_identity
from zaddesh_core.models import Device
import pytest


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
