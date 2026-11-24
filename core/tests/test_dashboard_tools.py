from datetime import timedelta

from zaddesh_core.endpoint_tracing import endpoints
from zaddesh_core.incidents import utc_now
from zaddesh_core.models import Device, Event, Check, CheckState
from zaddesh_core.models.topology import TopologyDiagram, TopologyNode
from zaddesh_core.topology import parse_interfaces

ROOT = '/api/v1/dashboard-tools'


def inventory(db):
    device = Device(name='Lab Firewall', ip='192.0.2.8', is_monitored=True)
    db.add(device)
    db.flush()
    diagram = TopologyDiagram(name='Lab diagram', published=False)
    db.add(diagram)
    db.flush()
    now = utc_now()
    ports = {'42': {'name': 'WAN 1', 'status': 'up', 'at': now.isoformat(), 'connector_present': True,
                     'interface_type': '6', 'rx_bps': 1000, 'tx_bps': 2000}}
    bindings = {'1.3.6.1.2.1.17.1.4.1.2.8': '42', '1.3.6.1.2.1.17.4.3.1.2.0.17.34.51.68.85': '8',
                '1.3.6.1.2.1.17.4.3.1.3.0.17.34.51.68.85': '3',
                '1.3.6.1.2.1.4.22.1.2.42.192.0.2.40': '00:11:22:33:44:55'}
    observations = endpoints(bindings, ports, now)
    db.add(TopologyNode(diagram_id=diagram.id, device_id=device.id, key='lab', label='Lab Firewall', kind='firewall',
        metadata_json={'interfaces': ports, 'endpoints': observations, 'observed_at': now.isoformat()+'Z'}))
    db.commit()
    return device, diagram


def test_endpoint_evidence_correlates_mac_ip_port_and_verified_username(client, db_session, admin_headers, viewer_headers):
    device, _ = inventory(db_session)
    db_session.add(Event(device_id=device.id, event_type='radius_accept', title='Authenticated',
                         details={'username':'net-user', 'mac':'0011.2233.4455', 'ip':'192.0.2.40'}))
    db_session.commit()
    for query in ('00:11:22:33:44:55', '001122334455', '192.0.2.40', 'net-user'):
        response = client.get(ROOT+'/endpoints', params={'q':query}, headers=admin_headers)
        assert response.status_code == 200
        [entry] = response.json()['entries']
        assert (entry['device_id'],entry['port'],entry['username']) == (device.id, 'WAN 1', 'net-user')
        assert entry['fresh'] and entry['association'] == 'Candidate access port'
    assert client.get(ROOT+'/endpoints', headers=viewer_headers).status_code == 403
    assert client.get(ROOT+'/endpoints', headers={}).status_code == 401


def test_static_self_multicast_and_missing_entries_are_not_endpoints():
    now = utc_now()
    rows = {'1.3.6.1.2.1.17.4.3.1.2.0.17.34.51.68.85':'3',
            '1.3.6.1.2.1.17.4.3.1.3.0.17.34.51.68.85':'4',
            '1.3.6.1.2.1.17.4.3.1.2.1.17.34.51.68.85':'3'}
    assert endpoints(rows, {}, now) == []
    assert endpoints({}, {}, now) == []
    arp = endpoints({'1.3.6.1.2.1.4.22.1.2.3.192.0.2.4':'00:11:22:33:44:55'}, {}, now)
    assert arp[0]['evidence'] == 'IP-MIB ARP'


def test_interface_rate_deltas_and_resets_are_not_capacity_estimates():
    now = utc_now()
    values = {'1.3.6.1.2.1.31.1.1.1.1.42':'WAN 1', '1.3.6.1.2.1.31.1.1.1.6.42':'2000',
              '1.3.6.1.2.1.31.1.1.1.10.42':'4000', '1.3.6.1.2.1.2.2.1.8.42':'1',
              '1.3.6.1.2.1.31.1.1.1.17.42':'1', '1.3.6.1.2.1.2.2.1.3.42':'6'}
    prior = {'42':{'at':(now-timedelta(seconds=10)).isoformat(),'rx':1000,'tx':2000,'discontinuity':None}}
    port = parse_interfaces(values, prior, now)['42']
    assert port['rx_bps'] == 800 and port['tx_bps'] == 1600 and port['speed'] is None
    assert port['connector_present'] is True
    values['1.3.6.1.2.1.31.1.1.1.6.42'] = '1'
    assert parse_interfaces(values, prior, now)['42']['rx_bps'] is None


def test_dashboard_owner_scoping_and_widget_validation_are_atomic(client, db_session, admin_headers, viewer_headers):
    device, diagram = inventory(db_session)
    response = client.post(ROOT+'/dashboards', headers=admin_headers, json={'title':'Operations','pinned':True})
    assert response.status_code == 201
    identifier = response.json()['id']
    path = f'{ROOT}/dashboards/{identifier}/widgets'
    widgets = [{'title':'Actual diagram','kind':'diagram','diagram_id':diagram.id},
               {'title':'WAN usage','kind':'firewall_usage','device_id':device.id,'interfaces':['42']}]
    assert client.put(path,headers=admin_headers,json=widgets).status_code == 200
    assert [row['title'] for row in client.get(path,headers=admin_headers).json()] == ['Actual diagram','WAN usage']
    assert client.get(path,headers=viewer_headers).status_code == 404
    assert client.put(path,headers=viewer_headers,json=widgets).status_code == 403
    assert client.put(path,headers=admin_headers,json=[{'title':'Invalid','kind':'metric','device_id':999999}]).status_code == 422
    assert len(client.get(path,headers=admin_headers).json()) == 2
    assert client.delete(f'{ROOT}/dashboards/{identifier}',headers=viewer_headers).status_code==403
    assert client.delete(f'{ROOT}/dashboards/{identifier}',headers=admin_headers).status_code==200
    assert client.get(path,headers=admin_headers).status_code==404


def test_wan_configuration_creates_one_rotating_check_and_preserves_existing_checks(client, db_session, admin_headers):
    device, _ = inventory(db_session)
    old = Check(name='Existing SNMP', device_id=device.id, check_type='snmp', target=device.ip,
                params={'version':'3'}, interval_seconds=60)
    db_session.add(old)
    db_session.commit()
    config = {'enabled':True,'interval_seconds':600,'max_bytes':1000000,'targets':[{
        'index':'42','name':'WAN 1','source_ip':'192.0.2.101','download_url':'http://192.0.2.100/download'}]}
    path = f'{ROOT}/wan-tests/{device.id}'
    for _ in range(2):
        assert client.put(path,headers=admin_headers,json=config).status_code == 200
    assert db_session.query(Check).filter_by(device_id=device.id).count() == 2
    db_session.expire_all()
    assert db_session.get(Check,old.id).params == {'version':'3'}
    assert client.get(path,headers=admin_headers).json()['enabled']
    config['targets'][0]['index'] = '999'
    assert client.put(path,headers=admin_headers,json=config).status_code == 422


def test_wan_failures_never_become_firewall_outages(client,db_session,admin_headers,monkeypatch):
    from zaddesh_core.config import settings
    from secrets import token_hex
    token=token_hex(32)
    monkeypatch.setattr(settings.collector,'shared_secret',token)
    device,_=inventory(db_session)
    device.status='up'
    check=Check(name='WAN test',device_id=device.id,target=device.ip,check_type='http',params={'operation':'wan_speed_test'})
    db_session.add(check)
    db_session.flush()
    db_session.add(CheckState(check_id=check.id))
    db_session.commit()
    response=client.post('/api/v1/metrics/ingest',headers={'Authorization':'Bearer '+token},json={'points':[{
        'device_id':device.id,'check_id':check.id,'metric_key':'wan.test.42.ok','value':0,'tags':{'interface_index':'42','reason':'Source IP unavailable'}}]})
    assert response.status_code==202
    db_session.expire_all()
    assert db_session.get(Device,device.id).status=='up'
    assert db_session.get(CheckState,check.id).current_status=='warning'


def test_interface_view_does_not_scan_metric_history_without_wan_tests(db_session):
    from sqlalchemy import event
    from zaddesh_core.api.v1.dashboard_tools import interfaces
    inventory(db_session)
    statements=[]
    connection=db_session.connection()
    def record(conn,cursor,statement,parameters,context,executemany):
        statements.append(statement)
    event.listen(connection,'before_cursor_execute',record)
    try:
        assert interfaces(db=db_session)[0]['interfaces'][0]['name']=='WAN 1'
    finally:
        event.remove(connection,'before_cursor_execute',record)
    assert not any('metric_raw' in statement.lower() for statement in statements)


def test_ip_queries_cannot_be_misinterpreted_as_mac_addresses():
    from zaddesh_core.endpoint_tracing import mac_address
    assert mac_address('192.168.100.100') is None
    assert mac_address('0011.2233.4455')=='00:11:22:33:44:55'


def test_counter_width_changes_do_not_create_false_wan_usage_spikes():
    now=utc_now()
    prior={'42':{'at':(now-timedelta(seconds=10)).isoformat(),'rx':100,'tx':100,'discontinuity':None,'counter_source':'32/32'}}
    values={'1.3.6.1.2.1.31.1.1.1.1.42':'WAN','1.3.6.1.2.1.31.1.1.1.6.42':str(2**40),
            '1.3.6.1.2.1.31.1.1.1.10.42':str(2**40)}
    assert parse_interfaces(values,prior,now)['42']['rx_bps'] is None
