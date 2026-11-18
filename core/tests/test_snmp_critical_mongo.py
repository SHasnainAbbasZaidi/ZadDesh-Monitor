import os
from datetime import timedelta, timezone

import pytest
from sqlalchemy import select

from tests.test_mongo_store import mongo as mongo
from tests.test_mongo_store import mongo_api as mongo_api
from zaddesh_core.api.v1.metrics import ingest_metrics
from zaddesh_core.api.v1.incidents import joined_alerts, alert_category
from zaddesh_core.config import settings
from zaddesh_core.incidents import utc_now
from zaddesh_core.models import Alert, Check, CheckState, Device
from zaddesh_core.models.incidents import Incident
from zaddesh_core.mongo_store import MongoSession
from zaddesh_core.schemas.metric import MetricIngestBatch

pytestmark = pytest.mark.skipif(not os.environ.get('ZADDESH_MONGO_TEST_URI'), reason='Requires isolated MongoDB validation database')


def test_real_mongodb_snmp_alarm_episode_dedup_priority_and_recovery(mongo, monkeypatch):
    monkeypatch.setattr(settings.features, 'incident_workspace', True)
    with MongoSession() as db:
        device = Device(name='Native SNMP lab', ip='192.0.2.99', is_priority=True)
        db.add(device)
        db.flush()
        check = Check(device_id=device.id, name='Fan state', check_type='snmp', target=device.ip, params={'version': '3'})
        db.add(check)
        db.flush()
        db.add(CheckState(check_id=check.id))
        db.commit()
        device_id, check_id = device.id, check.id
    start = utc_now()-timedelta(seconds=30)
    def emit(value, offset):
        with MongoSession() as db:
            batch = MetricIngestBatch.model_validate({'points': [{'device_id': device_id, 'check_id': check_id,
                'metric_key': 'snmp.1.3.6.1.4.1.9.9.13.1.4.1.3.1', 'value': value,
                'timestamp': (start+timedelta(seconds=offset)).replace(tzinfo=timezone.utc).isoformat()}]})
            return ingest_metrics(batch, authorized=None, db=db)
    assert emit(3, 0)['ingested'] == 1
    assert emit(3, 0)['duplicates'] == 1
    with MongoSession() as db:
        alerts = db.scalars(select(Alert)).all()
        assert len(alerts) == 1 and alerts[0].title == 'Fan Failure'
        alert_id = alerts[0].id
        assert db.get(Incident, alert_id).occurrence_count == 1
        assert joined_alerts(db).filter(alert_category() == 'other').count() == 1
    assert emit(1, 10)['ingested'] == 1
    with MongoSession() as db:
        assert db.get(Alert, alert_id).status == 'resolved'


def test_native_critical_filter_keeps_high_impact_faults_and_excludes_access_ports(mongo_api):
    client, headers = mongo_api
    with MongoSession() as db:
        device = Device(name='Native priority', ip='192.0.2.98', is_priority=True)
        db.add(device)
        db.flush()
        for title, severity in [('Fan Failure', 'critical'), ('VPN Tunnel Down', 'high'),
                                ('Interface Gi0/35: Link down', 'critical'), ('Fan Failure', 'warning')]:
            db.add(Alert(device_id=device.id, title=title, severity=severity))
        db.commit()
    response = client.get('/api/v1/incidents/alerts?category=critical&view=current&include_count=true', headers=headers)
    assert response.status_code == 200, response.text
    assert {row['title'] for row in response.json()['alerts']} == {'Fan Failure', 'VPN Tunnel Down'}
    assert response.json()['total'] == 2
    from zaddesh_core.email_worker import poll_native
    from zaddesh_core.email_alerts import ServerConfig, RuleInput, RecipientInput
    from zaddesh_core.models.email_alerts import EmailSettings, EmailRule, EmailRecipient, EmailDelivery
    with MongoSession() as db:
        state = EmailSettings(id=1, config=ServerConfig(host='127.0.0.1', port=19099,
            encryption='none', sender='monitor@example.com', enabled=True).model_dump())
        db.add(state)
        db.add(EmailRecipient(address_key='operator@example.com', config=RecipientInput(name='Operator', email='operator@example.com').model_dump()))
        db.add(EmailRule(config=RuleInput(name='Native rule', recipient_ids=[1], problem_types=['hardware']).model_dump()))
        db.commit()
    for _ in range(2):
        with MongoSession() as db:
            poll_native(db, db.get(EmailSettings, 1), int(utc_now().replace(tzinfo=timezone.utc).timestamp()))
            db.commit()
    with MongoSession() as db:
        assert db.query(EmailDelivery).count() == 1
