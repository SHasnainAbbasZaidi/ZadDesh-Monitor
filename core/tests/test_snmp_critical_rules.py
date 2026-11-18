from datetime import timedelta, timezone
from secrets import token_hex

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from zaddesh_core.config import settings
from zaddesh_core.database import Base
from zaddesh_core.incidents import SNMP_ALARM_STATES, utc_now
from zaddesh_core.models import Alert, AlertRule, Check, CheckState, Device
from zaddesh_core.models.incidents import Incident, IncidentDelivery


@pytest.fixture
def snmp_lab(client, db_session, monkeypatch):
    monkeypatch.setattr(settings.features, 'incident_workspace', True)
    key = token_hex(32)
    monkeypatch.setattr(settings.collector, 'shared_secret', key)
    device = Device(name='Lab switch', ip='192.0.2.81', is_priority=True, is_monitored=True)
    db_session.add(device)
    db_session.flush()
    check = Check(device_id=device.id, name='SNMP lab', target=device.ip, check_type='snmp',
                  params={'version': '3'}, interval_seconds=30)
    db_session.add(check)
    db_session.flush()
    db_session.add(CheckState(check_id=check.id))
    db_session.commit()

    def emit(metric, value, at, tags=None):
        response = client.post('/api/v1/metrics/ingest', headers={'Authorization': 'Bearer '+key}, json={'points': [{
            'device_id': device.id, 'check_id': check.id, 'metric_key': metric, 'value': value,
            'timestamp': at.replace(tzinfo=timezone.utc).isoformat(), 'tags': tags or {},
        }]})
        assert response.status_code == 202, response.text
    return device, check, emit


@pytest.mark.parametrize('prefix', list(SNMP_ALARM_STATES))
def test_agent_alarm_lifecycle_dedup_unknown_ack_recovery(snmp_lab, db_session, client, admin_headers, prefix):
    device, check, emit = snmp_lab
    title, failed, healthy = SNMP_ALARM_STATES[prefix]
    metric, start = 'snmp.'+prefix+('0' if prefix.endswith('2021.4.100.') else '1'), utc_now()-timedelta(seconds=90)
    failure, recovery = min(failed), min(healthy)
    emit(metric, failure, start)
    emit(metric, failure, start)
    [alert] = db_session.query(Alert).filter_by(device_id=device.id).all()
    assert alert.title == title and device.ip in alert.message
    assert db_session.get(Incident, alert.id).occurrence_count == 1
    feed = client.get('/api/v1/incidents/alerts?view=current&category=critical', headers=admin_headers).json()
    assert alert.id in {row['id'] for row in feed['alerts']}
    assert client.post('/api/v1/incidents/actions', headers=admin_headers,
                       json={'ids': [alert.id], 'action': 'acknowledge'}).status_code == 200
    emit(metric, 99, start+timedelta(seconds=10))
    emit(metric, recovery, start+timedelta(seconds=20), {'supported': 'false'})
    db_session.expire_all()
    assert db_session.get(Alert, alert.id).status == 'acknowledged'
    emit(metric, recovery, start+timedelta(seconds=30))
    db_session.expire_all()
    assert db_session.get(Alert, alert.id).status == 'resolved'
    emit(metric, failure, start+timedelta(seconds=60))
    assert db_session.query(Alert).filter_by(device_id=device.id).count() == 1
    db_session.expire_all()
    assert db_session.get(Alert, alert.id).status == 'active'


@pytest.mark.parametrize('version', ['2c', '3'])
@pytest.mark.parametrize('name', ['Cisco Switch', 'Windows Server', 'Linux Server', 'Router', 'Firewall', 'IP Camera', 'NVR'])
def test_saved_thresholds_work_across_categories_without_config_changes(snmp_lab, db_session, name, version):
    device, check, emit = snmp_lab
    device.name, check.params = name, {'version': version}
    metric = 'snmp.1.3.6.1.2.1.25.3.3.1.2.1'
    rule = AlertRule(name='High CPU Usage', metric_key=metric, threshold=93, duration_seconds=0,
                     operator='gt', severity='high')
    db_session.add(rule)
    db_session.commit()
    now = utc_now()-timedelta(seconds=5)
    emit(metric, 93, now)
    assert db_session.query(Alert).filter_by(device_id=device.id).count() == 0
    emit(metric, 94, now+timedelta(seconds=1))
    [alert] = db_session.query(Alert).filter_by(device_id=device.id).all()
    assert alert.rule_id == rule.id and alert.title == 'High CPU Usage' and alert.severity == 'high'
    assert rule.threshold == 93 and rule.duration_seconds == 0 and check.params == {'version': version}


def test_duration_priority_maintenance_disabled_rules_and_late_data(snmp_lab, db_session, monkeypatch):
    from zaddesh_core.models import MaintenanceWindow
    device, check, emit = snmp_lab
    monkeypatch.setattr(settings.features, 'incident_webhook_url', 'http://127.0.0.1:19099/fixture')
    metric, start = 'snmp.1.3.6.1.2.1.25.3.3.1.2.1', utc_now()-timedelta(seconds=180)
    rule = AlertRule(name='High CPU Usage', metric_key=metric, threshold=90, duration_seconds=60, severity='critical')
    db_session.add(rule)
    db_session.add(MaintenanceWindow(name='Lab', scope_type='device', scope_id=device.id,
        start_time=start, end_time=start+timedelta(seconds=100)))
    db_session.commit()
    for offset in (0, 30):
        emit(metric, 99, start+timedelta(seconds=offset))
    assert db_session.query(Alert).filter_by(device_id=device.id).count() == 0
    emit(metric, 99, start+timedelta(seconds=60))
    [alert] = db_session.query(Alert).filter_by(device_id=device.id).all()
    assert db_session.get(Incident, alert.id).suppression == 'maintenance'
    assert db_session.query(IncidentDelivery).count() == 0
    emit(metric, 99, start+timedelta(seconds=120))
    assert db_session.query(IncidentDelivery).count() == 1
    emit(metric, 20, start+timedelta(seconds=130))
    emit(metric, 99, start+timedelta(seconds=61))
    db_session.expire_all()
    assert db_session.get(Alert, alert.id).status == 'resolved'
    device.is_priority = False
    db_session.commit()
    emit('snmp.1.3.6.1.4.1.9.9.13.1.4.1.3.9', 3, start+timedelta(seconds=140))
    assert db_session.query(Alert).filter_by(device_id=device.id).count() == 1


def test_snmp_timeout_and_unsupported_are_not_device_outages(snmp_lab, client, admin_headers, db_session):
    device, check, emit = snmp_lab
    start = utc_now()-timedelta(seconds=10)
    emit('snmp.available', 0, start)
    db_session.expire_all()
    assert db_session.get(Device, device.id).status == 'unknown'
    [alert] = db_session.query(Alert).filter_by(device_id=device.id).all()
    assert alert.title == 'SNMP Unreachable' and alert.severity == 'warning'
    emit('snmp.supported', 0, start+timedelta(seconds=1))
    db_session.expire_all()
    assert db_session.get(CheckState, check.id).current_status == 'unknown'
    assert client.get('/api/v1/incidents/alerts?category=critical', headers=admin_headers).json()['alerts'] == []


def test_disabled_rule_overrides_default_and_duplicate_rules_do_not_flood(snmp_lab, db_session):
    device, check, emit = snmp_lab
    metric = 'snmp.1.3.6.1.4.1.9.9.13.1.4.1.3.1'
    rule = AlertRule(name='Fan Failure', metric_key=metric, operator='eq', threshold=3,
                     duration_seconds=0, severity='critical', is_enabled=False)
    db_session.add(rule)
    db_session.commit()
    start = utc_now()-timedelta(seconds=4)
    emit(metric, 3, start)
    assert db_session.query(Alert).filter_by(device_id=device.id).count() == 0
    rule.is_enabled = True
    db_session.add(AlertRule(name='Duplicate fan rule', metric_key=metric, operator='eq', threshold=3,
                            duration_seconds=0, severity='critical'))
    db_session.commit()
    emit(metric, 3, start+timedelta(seconds=1))
    assert db_session.query(Alert).filter_by(device_id=device.id).count() == 1


def test_snapshots_feed_existing_critical_rules_without_duplicate_modules(snmp_lab, db_session):
    from zaddesh_core.incidents import evaluate_snmp_observation
    device, check, _ = snmp_lab
    evaluate_snmp_observation(db_session, check, device, {'1.3.6.1.4.1.9.9.13.1.4.1.3.5': '3'}, utc_now())
    db_session.commit()
    [alert] = db_session.query(Alert).filter_by(device_id=device.id).all()
    assert alert.title == 'Fan Failure'


def test_counter_rates_need_baselines_and_resets_are_not_spikes(snmp_lab, db_session):
    device, check, emit = snmp_lab
    metric, start = 'snmp.1.3.6.1.2.1.2.2.1.14.1', utc_now()-timedelta(seconds=90)
    db_session.add(AlertRule(name='Interface Errors Critical', metric_key=metric, condition_type='rate',
                            threshold=.1, duration_seconds=0, severity='critical'))
    db_session.commit()
    emit(metric, 100, start)
    assert db_session.query(Alert).filter_by(device_id=device.id).count() == 0
    emit(metric, 106, start+timedelta(seconds=30))
    [alert] = db_session.query(Alert).filter_by(device_id=device.id).all()
    assert alert.value == pytest.approx(.2)
    emit(metric, 1, start+timedelta(seconds=60))
    db_session.expire_all()
    assert db_session.get(Alert, alert.id).status == 'active'
    emit(metric, 1, start+timedelta(seconds=90))
    db_session.expire_all()
    assert db_session.get(Alert, alert.id).status == 'resolved'


def test_legacy_view_also_distinguishes_snmp_communication_from_device_failure(snmp_lab, db_session, monkeypatch):
    device, check, emit = snmp_lab
    monkeypatch.setattr(settings.features, 'incident_workspace', False)
    start = utc_now()-timedelta(seconds=4)
    emit('snmp.available', 0, start)
    [alert] = db_session.query(Alert).filter_by(device_id=device.id).all()
    assert (alert.title, alert.severity) == ('SNMP Unreachable', 'warning')
    db_session.expire_all()
    assert db_session.get(Device, device.id).status == 'unknown'
    emit('snmp.available', 1, start+timedelta(seconds=1))
    db_session.expire_all()
    assert db_session.get(Alert, alert.id).status == 'resolved'


def test_native_notifications_use_real_smtp_existing_queue_without_zabbix(tmp_path, monkeypatch):
    from tests.test_email_alerts import receiver
    from zaddesh_core import email_worker
    from zaddesh_core.email_alerts import ServerConfig, RecipientInput, RuleInput
    from zaddesh_core.models.email_alerts import EmailSettings, EmailRule, EmailRecipient, EmailDelivery
    engine = create_engine(f'sqlite:///{(tmp_path/"native-mail.db").as_posix()}')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(email_worker, 'SessionLocal', sessions)
    now = int(utc_now().replace(tzinfo=timezone.utc).timestamp())
    with receiver(failures=1) as smtp:
        with sessions.begin() as db:
            device = Device(name='Critical switch', ip='192.0.2.91', is_priority=True)
            db.add(device)
            db.flush()
            db.add(Alert(device_id=device.id, title='Fan Failure', severity='critical', triggered_at=utc_now()))
            db.add(EmailSettings(id=1, config=ServerConfig(host='127.0.0.1', port=smtp.server_address[1],
                encryption='none', sender='monitor@example.com', enabled=True).model_dump()))
            db.add(EmailRecipient(address_key='operator@example.com', config=RecipientInput(name='Operator',
                email='operator@example.com').model_dump()))
            rule = RuleInput(name='Existing rule', recipient_ids=[1], problem_types=['hardware']).model_dump()
            db.add_all([EmailRule(config=rule), EmailRule(config=rule)])
        email_worker.poll_once(now+1)
        email_worker.poll_once(now+26)
        with sessions() as db:
            assert len(db.scalars(select(EmailDelivery)).all()) == 1
        email_worker.send_once(now+27)
        email_worker.send_once(now+58)
        assert len(smtp.received) == 1 and b'Fan Failure' in smtp.received[0] and b'192.0.2.91' in smtp.received[0]
        with sessions.begin() as db:
            alert = db.scalar(select(Alert))
            alert.status, alert.resolved_at = 'resolved', utc_now()+timedelta(seconds=60)
        email_worker.poll_once(now+61)
        email_worker.send_once(now+64)
        assert len(smtp.received) == 2
    engine.dispose()


def test_native_email_intake_advances_past_first_batch(snmp_lab, db_session):
    from zaddesh_core.email_worker import poll_native
    from zaddesh_core.email_alerts import ServerConfig, RecipientInput, RuleInput
    from zaddesh_core.models.email_alerts import EmailSettings, EmailRule, EmailRecipient, EmailDelivery
    device, _, _ = snmp_lab
    now = int(utc_now().replace(tzinfo=timezone.utc).timestamp())
    state = EmailSettings(id=1, config=ServerConfig(host='127.0.0.1', port=19099, encryption='none',
        sender='monitor@example.com', enabled=True).model_dump())
    db_session.add(state)
    recipient = EmailRecipient(address_key='operator@example.com', config=RecipientInput(name='Operator', email='operator@example.com').model_dump())
    db_session.add(recipient)
    db_session.flush()
    db_session.add(EmailRule(config=RuleInput(name='Native intake', recipient_ids=[recipient.id], problem_types=['hardware']).model_dump()))
    db_session.add_all([Alert(device_id=device.id, title=f'Fan Failure {index}', severity='critical', triggered_at=utc_now()) for index in range(201)])
    db_session.commit()
    poll_native(db_session, state, now+1)
    db_session.commit()
    assert db_session.query(EmailDelivery).count() == 200
    poll_native(db_session, state, now+26)
    db_session.commit()
    assert db_session.query(EmailDelivery).count() == 201
