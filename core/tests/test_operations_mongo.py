import os
import pytest
from tests.test_mongo_store import mongo as mongo, mongo_api as mongo_api
from tests.test_dashboard_tools import inventory, ROOT
from zaddesh_core.mongo_store import MongoSession

pytestmark = pytest.mark.skipif(not os.environ.get('ZADDESH_MONGO_TEST_URI'), reason='Requires isolated MongoDB validation database')


def test_native_operations_dashboard_json_and_wan_check_roundtrip(mongo_api):
    client, headers = mongo_api
    with MongoSession() as db:
        device, diagram = inventory(db)
        device_id, diagram_id = device.id, diagram.id
    created = client.post(ROOT+'/dashboards', headers=headers, json={'title':'Native operations','pinned':True})
    assert created.status_code == 201, created.text
    identifier = created.json()['id']
    widgets = [{'kind':'diagram','title':'Actual topology','diagram_id':diagram_id,'width':2},
               {'kind':'firewall_usage','title':'WAN share','device_id':device_id,'interfaces':['42']}]
    path = f'{ROOT}/dashboards/{identifier}/widgets'
    assert client.put(path,headers=headers,json=widgets).status_code == 200
    rows = client.get(path,headers=headers).json()
    assert rows[0]['diagram_id'] == diagram_id and rows[0]['width'] == 2
    assert client.get(ROOT+'/dashboards',headers=headers).json()[0]['pinned'] is True
    assert client.get(ROOT+'/endpoints?q=192.0.2.40',headers=headers).json()['entries'][0]['port']=='WAN 1'
    config = {'enabled':True,'targets':[{'index':'42','name':'WAN','source_ip':'192.0.2.101','download_url':'http://192.0.2.100/download'}]}
    path = f'{ROOT}/wan-tests/{device_id}'
    response = client.put(path,headers=headers,json=config)
    assert response.status_code==200, response.text
    assert client.get(path,headers=headers).json()['targets'][0]['source_ip']=='192.0.2.101'
    response = client.get(ROOT+'/interfaces',headers=headers)
    assert response.status_code==200, response.text
    assert response.json()[0]['interfaces'][0]['rx_bps']==1000

    from zaddesh_core.api.v1.metrics import ingest_metrics
    from zaddesh_core.models import Check, CheckState, Device
    from zaddesh_core.schemas.metric import MetricIngestBatch
    with MongoSession() as db:
        probe=Check(device_id=device_id,name='SNMP availability',check_type='snmp',target='192.0.2.8')
        db.add(probe)
        db.flush()
        db.add(CheckState(check_id=probe.id))
        db.commit()
        probe_id=probe.id
    with MongoSession() as db:
        batch=MetricIngestBatch.model_validate({'points':[{'device_id':device_id,'check_id':probe_id,'metric_key':'snmp.available','value':1}]})
        ingest_metrics(batch,authorized=None,db=db)
    with MongoSession() as db:
        assert db.get(Device,device_id).status=='up'
