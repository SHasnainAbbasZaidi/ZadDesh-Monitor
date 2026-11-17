import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from secrets import token_hex
from socketserver import StreamRequestHandler, ThreadingTCPServer
import ssl
from threading import Thread
from time import monotonic, sleep
from zoneinfo import ZoneInfo

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from zaddesh_core import email_worker, main
from zaddesh_core.api.v1.email_alerts import csv_text
from zaddesh_core.config import settings
from zaddesh_core.database import Base, get_db
from zaddesh_core.email_alerts import RecipientInput, RuleInput, ServerConfig, scheduled
from zaddesh_core.email_smtp import smtp_operation
from zaddesh_core.models.email_alerts import EmailAttempt, EmailDelivery, EmailRecipient, EmailRule, EmailSettings
from zaddesh_core.models.auth import User
from zaddesh_core.models.zabbix import ZabbixConnection
from zaddesh_core.security import create_access_token


class SMTPReceiver(StreamRequestHandler):
    def respond(self, value):
        self.wfile.write(value + b"\r\n")

    def secure(self):
        try:
            self.connection = self.server.tls.wrap_socket(self.connection, server_side=True)
        except ssl.SSLError:
            return False
        self.rfile = self.connection.makefile("rb")
        self.wfile = self.connection.makefile("wb", buffering=0)
        return True

    def handle(self):
        secured = self.server.implicit
        if secured and not self.secure():
            return
        self.respond(b"220 test receiver ready")
        while line := self.rfile.readline(65536):
            command = line.strip()
            verb = command.upper()
            if verb.startswith(b"EHLO"):
                self.respond(b"250-loopback")
                if self.server.tls and not secured:
                    self.respond(b"250-STARTTLS")
                self.respond(b"250 AUTH PLAIN XOAUTH2")
            elif verb == b"STARTTLS":
                self.respond(b"220 start TLS")
                if not self.secure():
                    return
                secured = True
            elif verb.startswith(b"AUTH "):
                _, mechanism, encoded = command.split()
                credentials = base64.b64decode(encoded)
                expected = (b"\0lab\0" + self.server.credential if mechanism == b"PLAIN"
                            else b"user=lab\x01auth=Bearer " + self.server.credential + b"\x01\x01")
                self.respond(b"235 authenticated" if secured and credentials == expected else b"535 authentication refused")
            elif verb.startswith(b"MAIL FROM") or verb.startswith(b"RCPT TO"):
                self.respond(b"250 ok")
            elif verb == b"DATA":
                self.respond(b"354 send message")
                data = []
                while (part := self.rfile.readline(65536)) and part != b".\r\n":
                    data.append(part)
                if self.server.temporary_failures:
                    self.server.temporary_failures -= 1
                    self.respond(b"451 temporary rejection")
                else:
                    self.server.received.append(b"".join(data))
                    self.respond(b"250 accepted")
            else:
                self.respond(b"500 unsupported")


@contextmanager
def receiver(tls=None, failures=0, implicit=False):
    with ThreadingTCPServer(("127.0.0.1", 0), SMTPReceiver) as server:
        server.daemon_threads = True
        server.tls, server.temporary_failures, server.received = tls, failures, []
        server.implicit = implicit
        server.credential = token_hex(16).encode()
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture(autouse=True)
def isolate_live_worker(monkeypatch):
    async def disabled(_):
        return
    monkeypatch.setattr(main, "email_loop", disabled)
    monkeypatch.setattr(settings, "demo_mode", False)


def test_admin_permissions_secrets_templates_and_simulation(client, db_session, admin_headers, viewer_headers, monkeypatch):
    base = "http://127.0.0.1/api/v1/email-alerts"
    assert client.get(base, headers=viewer_headers).status_code == 403
    assert client.post(base + "/test-email", headers=viewer_headers, json={"recipient_ids": [1]}).status_code == 403
    monkeypatch.setattr(settings.security, "encryption_key", token_hex(32))
    credential = token_hex(16)
    record = {"host": "mail.example.com", "sender": "monitor@example.com", "auth": "password", "username": "monitor@example.com", "credential": credential}
    result = client.put(base + "/server", headers=admin_headers, json=record)
    assert result.status_code == 200 and result.json()["has_credential"]
    assert credential not in result.text and credential not in db_session.get(EmailSettings, 1).secret
    assert client.put(base + "/server", headers=admin_headers, json={**record, "credential": None, "host": "other.example.com"}).status_code == 422
    assert client.put(base + "/server", headers=admin_headers, json={**record, "encryption": "none"}).status_code == 422
    entry = {"name": "Network engineer", "email": "engineer@example.com", "groups": ["Network Team"]}
    stored = client.post(base + "/recipients", headers=admin_headers, json=entry)
    assert stored.status_code == 201
    assert client.post(base + "/recipients", headers=admin_headers, json={**entry, "email": "ENGINEER@example.com"}).status_code == 409
    assert client.post(base + "/rules", headers=admin_headers, json={"name": "Network", "groups": ["Missing"]}).status_code == 422
    assert client.post(base + "/rules", headers=admin_headers, json={"name": "Network", "groups": ["Network Team"], "categories": ["Network"]}).status_code == 201
    simulated = client.post(base + "/simulate", headers=admin_headers, json={})
    assert simulated.json()["matches"][0]["recipients"][0]["email"] == entry["email"]
    html = {"subject": "{{device_name}}", "text": "{{problem}}", "html": '<img src=x onerror="alert(1)">'}
    assert client.put(base + "/templates/critical", headers=admin_headers, json=html).status_code == 422
    safe = client.post(base + "/preview", headers=admin_headers, json={"event": {"problem": "<script>unsafe</script>"}}).json()
    assert safe["subject"].startswith("[TEST]") and "<script>" not in safe["html"] and "&lt;script&gt;" in safe["html"]
    assert csv_text(" +SUM(A1:A3)").startswith("'")


def test_overnight_schedule():
    schedule = {"days": [0], "start": "22:00", "end": "06:00"}
    zone = ZoneInfo("Asia/Karachi")
    assert scheduled(schedule, datetime(2026, 11, 17, 2, tzinfo=zone))
    assert not scheduled(schedule, datetime(2026, 11, 17, 9, tzinfo=zone))


def tls_receiver(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False).sign(key, hashes.SHA256()))
    identity = tmp_path / "cert.pem"
    identity.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private = tmp_path / "key.pem"
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(identity, private)
    monkeypatch.setenv("SSL_CERT_FILE", str(identity))
    return context


def test_verified_starttls_password_and_xoauth2(tmp_path, monkeypatch):
    tls = tls_receiver(tmp_path, monkeypatch)
    with receiver(tls=tls) as smtp:
        config = ServerConfig(host="localhost", port=smtp.server_address[1], sender="monitor@example.com", auth="password", username="lab").model_dump()
        result = smtp_operation(config, smtp.credential.decode())
        assert result.result == "connected", result.message
        assert smtp_operation({**config, "auth": "oauth2"}, smtp.credential.decode()).result == "connected"
        assert smtp_operation(config, "incorrect").result == "authentication"
        assert smtp_operation({**config, "host": "127.0.0.1"}, smtp.credential.decode()).message == "SMTP TLS certificate verification failed"
    with receiver(tls=tls, implicit=True) as smtp:
        assert smtp_operation({**config, "port": smtp.server_address[1], "encryption": "tls"}, smtp.credential.decode()).result == "connected"


def test_test_email_is_delivered_by_the_running_core_without_a_ui(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'worker.db').as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions.begin() as db:
        db.add(User(id=1, username="lab-admin", email="admin@example.com", role="admin", hashed_password="unused"))
    headers = {"Authorization": "Bearer " + create_access_token({"sub": "lab-admin", "user_id": 1, "role": "admin"})}
    monkeypatch.setattr(main, "email_loop", email_worker.email_loop)
    monkeypatch.setattr(email_worker, "SessionLocal", sessions)

    def session_for_request():
        with sessions() as db:
            yield db

    monkeypatch.setitem(main.app.dependency_overrides, get_db, session_for_request)
    with receiver() as smtp, TestClient(main.app, base_url="http://127.0.0.1", client=("127.0.0.1", 49000)) as client:
        root = "/api/v1/email-alerts"
        assert client.put(root + "/server", headers=headers, json={"host": "127.0.0.1", "port": smtp.server_address[1],
            "encryption": "none", "sender": "monitor@example.com"}).status_code == 200
        human = client.post(root + "/recipients", headers=headers, json={"name": "Lab operator", "email": "operator@example.com"}).json()
        queued = client.post(root + "/test-email", headers=headers, json={"recipient_ids": [human["id"], human["id"]]})
        assert queued.status_code == 202 and len(queued.json()["queued"]) == 1
        row = root + f"/history/{queued.json()['queued'][0]}"
        deadline = monotonic() + 15
        while monotonic() < deadline:
            history = client.get(row, headers=headers).json()
            if history["status"] == "sent":
                break
            sleep(.2)
        assert history["status"] == "sent" and history["attempt_history"][0]["status"] == "sent"
        assert len(smtp.received) == 1 and b"[TEST]" in smtp.received[0]
        assert client.get(root + "/history.csv", headers=headers).status_code == 200
    engine.dispose()


def test_zabbix_lifecycle_multiple_recipients_retry_and_restart(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'mail.db').as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(email_worker, "SessionLocal", sessions)
    now = 1_795_000_000
    source = "http://192.0.2.9/api_jsonrpc.php"
    event = {"eventid": "100", "clock": str(now), "value": "1", "name": "Core uplink down", "severity": "4", "r_eventid": "0", "suppressed": "0",
             "hosts": [{"hostid": "11", "name": "Core switch"}], "tags": [{"tag": "team", "value": "network"}]}
    state = {"recovery": False, "missing": False}

    class Zabbix:
        version, token = "7.4.1", token_hex(16)

        def rows(self, method, params):
            if method == "host.get":
                return [{"hostid": "11", "name": "Core switch", "hostgroups": [{"groupid": "9"}],
                         "interfaces": [{"ip": "192.0.2.2", "main": "1"}]}]
            assert method == "event.get"
            if params.get("value") == 0:
                return [{"eventid": "200", "clock": str(now + 321), "value": "0"}] if state["recovery"] else []
            if state["missing"] or "eventid_from" in params:
                return []
            return [dict(event)]

    @contextmanager
    def saved(_):
        yield Zabbix()

    monkeypatch.setattr(email_worker, "zabbix_connection", saved)
    with receiver(failures=1) as smtp:
        config = ServerConfig(host="127.0.0.1", port=smtp.server_address[1], encryption="none", sender="monitor@example.com", enabled=True).model_dump()
        with sessions.begin() as db:
            db.add(EmailSettings(id=1, config=config, scan_from=now - 1))
            db.add(ZabbixConnection(id=1, api_url=source, version="7.4.1", encrypted_token="unused-by-fixture"))
            for index in (1, 2):
                recipient = RecipientInput(email=f"operator{index}@example.com", name=f"Operator {index}", groups=["Network Team"])
                db.add(EmailRecipient(address_key=recipient.email, config=recipient.model_dump()))
            rule = RuleInput(name="Core connectivity", groups=["Network Team"], host_group_ids=["9"], reminder_minutes=[5], tags={"team": "network"})
            db.add_all([EmailRule(config=rule.model_dump()), EmailRule(config=rule.model_dump())])
        email_worker.poll_once(now)
        email_worker.send_once(now + 1)
        email_worker.send_once(now + 7)
        email_worker.poll_once(now + 25)
        email_worker.send_once(now + 32)
        with sessions() as db:
            delivered = [(row.status, row.last_error) for row in db.scalars(select(EmailDelivery).order_by(EmailDelivery.id))]
            assert delivered == [("sent", None), ("sent", None)]
            assert [row.status for row in db.scalars(select(EmailAttempt).order_by(EmailAttempt.id))] == ["retrying", "sent", "sent"]
        event["severity"] = "5"
        email_worker.poll_once(now + 55)
        email_worker.send_once(now + 56)
        email_worker.send_once(now + 62)
        state["missing"] = True
        email_worker.poll_once(now + 90)
        with sessions() as db:
            assert len(list(db.scalars(select(EmailDelivery)))) == 4
        state["missing"] = False
        email_worker.poll_once(now + 300)
        email_worker.send_once(now + 301)
        email_worker.send_once(now + 307)
        state["recovery"] = True
        event["r_eventid"] = "200"
        email_worker.poll_once(now + 326)
        email_worker.send_once(now + 327)
        email_worker.send_once(now + 333)
        email_worker.poll_once(now + 353)
        with sessions() as db:
            notices = list(db.scalars(select(EmailDelivery).order_by(EmailDelivery.id)))
            assert [item.kind for item in notices] == ["opened"] * 2 + ["escalation"] * 2 + ["reminder"] * 2 + ["recovery"] * 2
            assert all(item.status == "sent" for item in notices)
        assert len(smtp.received) == 8
        assert len({message.split(b"Message-ID: ")[1].split(b"\r\n", 1)[0] for message in smtp.received}) == 8
    engine.dispose()
