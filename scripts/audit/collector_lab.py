"""Exercise the real Rust collector against local HTTP, TCP, ICMP, and TLS targets."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import time

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--collector", type=Path, required=True)
parser.add_argument("--work", type=Path, required=True, help="New directory for disposable data and logs")
parser.add_argument("--port", type=int, default=18772)
parser.add_argument("--snmp", action="store_true", help="Also launch a local unprivileged Net-SNMP agent")
args = parser.parse_args()
root = Path(__file__).resolve().parents[2]
work = args.work.resolve()
work.mkdir()
collector_binary = args.collector.resolve(strict=True)
base = f"http://127.0.0.1:{args.port}"
env = os.environ.copy()
env.update(ZADDESH_DATABASE__URL="sqlite:///" + (work / "lab.db").as_posix(),
           ZADDESH_COLLECTOR__ENROLLMENT_TOKEN=secrets.token_hex(32),
           ZADDESH_COLLECTOR_TOKEN=secrets.token_hex(32), ZADDESH_DEMO_MODE="false",
           ZADDESH_SECURITY__JWT_SECRET=secrets.token_hex(32),
           ZADDESH_SECURITY__ENCRYPTION_KEY=secrets.token_hex(32))
env["ZADDESH_COLLECTOR_ENROLLMENT_TOKEN"] = env["ZADDESH_COLLECTOR__ENROLLMENT_TOKEN"]
subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=root / "core", env=env, check=True)
flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
client = httpx.Client(base_url=base, timeout=3, trust_env=False)
processes = []
logs = []
servers = []
started = time.monotonic()
service_available = True


def log(name, **fields):
    print(json.dumps({"step": name, "elapsed_s": round(time.monotonic() - started, 2), **fields}), flush=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_HEAD(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        match self.path:
            case "/redirect":
                self.send_response(302)
                self.send_header("Location", "/redirect2")
                self.end_headers()
                return
            case "/redirect2":
                self.send_response(307)
                self.send_header("Location", "/")
                self.end_headers()
                return
            case "/slow":
                time.sleep(2.5)
            case _:
                pass
        self.send_response(404 if self.path == "/missing" else 200 if service_available else 503)
        self.end_headers()
        try:
            self.wfile.write(b"local target body")
        except OSError:
            pass


def target(tls=None):
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    if tls:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    Thread(target=server.serve_forever, daemon=True).start()
    servers.append(server)
    return server.server_port


def tls_context(label, expired):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=20))
            .not_valid_after(now + dt.timedelta(days=-1 if expired else 1)).sign(key, hashes.SHA256()))
    certificate = work / f"{label}.crt"
    private = work / f"{label}.key"
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, private)
    return context


def launch(name, command):
    stream = (work / f"{name}.log").open("ab")
    logs.append(stream)
    proc = subprocess.Popen(command, cwd=root, env=env, stdout=stream, stderr=stream, creationflags=flags)
    processes.append(proc)
    return proc


def core(name):
    return launch(name, [sys.executable, "-m", "uvicorn", "zaddesh_core.main:app",
                         "--host", "127.0.0.1", "--port", str(args.port), "--no-access-log", "--no-proxy-headers"])


def wait_for(predicate, seconds=25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            result = predicate()
            if result:
                return result
        except httpx.TransportError:
            pass
        if any(proc.poll() not in (None, 0, 1, -15) for proc in processes):
            raise RuntimeError("Lab process crashed; see service logs")
        time.sleep(.25)
    raise TimeoutError("Lab condition was not met")


def post(route, data, headers=None):
    return client.post("/api/v1/" + route, json=data, headers=headers).raise_for_status().json()


def get(route):
    return client.get("/api/v1/" + route, headers=admin).raise_for_status().json()


def add(name, protocol, address, params=None, timeout=5):
    device = post("devices", {"name": name, "ip": "127.0.0.1", "initial_check_types": []}, admin)
    check = post("checks", {"name": name, "device_id": device["id"], "check_type": protocol, "target": address,
                            "params": params or {}, "interval_seconds": 5, "timeout_seconds": timeout,
                            "retries": 0}, admin)
    return device["id"], check["id"]


def state(check_id):
    return get(f"checks/{check_id}")["state"]["current_status"]


def samples(device_id, check_id, key, start=None):
    return post("metrics/query", {"device_id": device_id, "check_id": check_id, "metric_keys": [key],
                                 "start_time": (start or dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=15)).isoformat()}, admin)["series"][0]["points"]


try:
    http_port = target()
    tls_port = target(tls_context("self-signed", False))
    expired_port = target(tls_context("expired", True))
    tcp = socket.socket()
    tcp.bind(("127.0.0.1", 0))
    tcp.listen()
    tcp_port = tcp.getsockname()[1]
    def send_banner():
        while True:
            try:
                connection, _ = tcp.accept()
                with connection:
                    connection.sendall(b"ZadDesh audit TCP banner\r\n")
            except OSError:
                return
    Thread(target=send_banner, daemon=True).start()
    service = core("core")
    wait_for(lambda: client.get("/readyz").is_success)
    identity = post("auth/setup", {"username": "labadmin", "email": "labadmin@example.com",
                                   "password": secrets.token_hex(24)})
    admin = {"Authorization": "Bearer " + identity["access_token"]}
    log("health_and_setup", health=client.get("/healthz").status_code, ready=client.get("/readyz").status_code)
    origin = f"http://127.0.0.1:{http_port}"
    suite = [
        ("HTTP body", "http", origin, {"body_contains": "local target"}, "ok", "http.available"),
        ("HTTP body mismatch", "http", origin, {"body_contains": "absent"}, "critical", "http.available"),
        ("HTTP redirect chain", "http", origin + "/redirect", {}, "ok", "http.status_code"),
        ("HTTP expected 404", "http", origin + "/missing", {"expected_status": 404}, "ok", "http.status_code"),
        ("HTTP HEAD", "http", origin, {"method": "HEAD", "expected_status": 204}, "ok", "http.status_code"),
        ("HTTP deadline", "http", origin + "/slow", {}, "critical", "http.available"),
        ("Untrusted TLS", "https", f"127.0.0.1:{tls_port}", {}, "critical", "http.available"),
        ("Expired untrusted TLS", "https", f"127.0.0.1:{expired_port}", {}, "critical", "http.available"),
        ("TCP loopback", "tcp", f"127.0.0.1:{tcp_port}", {}, "ok", "tcp.available"),
        ("ICMP IPv4", "icmp", "127.0.0.1", {"count": 2}, "ok", "icmp.rtt_avg"),
        ("ICMP IPv6", "icmp", "::1", {"count": 2}, "ok", "icmp.rtt_avg"),
    ]
    if args.snmp:
        snmp_port = 19161
        community = secrets.token_hex(18)
        auth_password, privacy_password = secrets.token_hex(18), secrets.token_hex(18)
        env.update(ZADDESH_SNMP_V1_COMMUNITY=community, ZADDESH_SNMP_V2_COMMUNITY=community,
                   ZADDESH_SNMP_AUTH_USERNAME="zdsAuth", ZADDESH_SNMP_PRIV_USERNAME="zdsPriv",
                   ZADDESH_SNMP_AUTH_AUTH_PASSWORD=auth_password,
                   ZADDESH_SNMP_PRIV_AUTH_PASSWORD=auth_password,
                   ZADDESH_SNMP_PRIV_PRIV_PASSWORD=privacy_password,
                   SNMP_PERSISTENT_DIR=str(work), MIBS="")
        configuration = work / "agent.conf"
        configuration.touch(mode=0o600)
        configuration.write_text(f"""agentAddress udp:127.0.0.1:{snmp_port},udp6:[::1]:{snmp_port}
rocommunity {community} 127.0.0.1
createUser zdsNoAuth
createUser zdsAuth SHA {auth_password}
createUser zdsPriv SHA {auth_password} AES {privacy_password}
rouser zdsNoAuth noauth
rouser zdsAuth auth
rouser zdsPriv priv
""", encoding="utf-8")
        launch("net-snmp-agent", ["snmpd", "-f", "-Lo", "-C", "-c", str(configuration),
                                 "-p", str(work / "net-snmp-agent.pid")])
        time.sleep(.5)
        for username, level, privacy in (("zdsAuth", "authNoPriv", []),
                                         ("zdsPriv", "authPriv", ["-x", "AES", "-X", privacy_password])):
            direct = subprocess.run(["snmpget", "-v3", "-l", level, "-u", username, "-a", "SHA",
                                     "-A", auth_password, *privacy, "-On", "-t", "2", "-r", "0",
                                     f"udp:127.0.0.1:{snmp_port}", "1.3.6.1.2.1.1.3.0"],
                                    env=env, capture_output=True)
            log("agent_preflight", auth_mode=level, status=direct.returncode)
        snmp_cases = (
            ("SNMPv1 GET", "V1", "1", "get", "1.3.6.1.2.1.1.3.0", {}, "1.3.6.1.2.1.1.3.0"),
            ("SNMPv2c GETNEXT", "V2", "2c", "getnext", "1.3.6.1.2.1.1.2.0", {}, "1.3.6.1.2.1.1.3.0"),
            ("SNMPv2c GETBULK", "V2", "2c", "getbulk", "1.3.6.1.2.1.2.2.1.10", {}, "1.3.6.1.2.1.2.2.1.10.1"),
            ("SNMPv2c WALK", "V2", "2c", "walk", "1.3.6.1.2.1.2.2.1.10", {}, "1.3.6.1.2.1.2.2.1.10.1"),
            ("SNMPv3 authNoPriv", "AUTH", "3", "get", "1.3.6.1.2.1.1.3.0", {"security_level": "authNoPriv", "auth_protocol": "sha1"}, "1.3.6.1.2.1.1.3.0"),
            ("SNMPv3 authPriv", "PRIV", "3", "get", "1.3.6.1.2.1.1.3.0", {"security_level": "authPriv", "auth_protocol": "sha1", "privacy_protocol": "aes128"}, "1.3.6.1.2.1.1.3.0"),
        )
        for name, credential, version, operation, oid, extra, key in snmp_cases:
            suite.append((name, "snmp", "127.0.0.1", {
                "credential": credential, "version": version, "operation": operation, "oid": oid, "port": snmp_port, **extra,
            }, "ok", "snmp." + key))
    checks = [(label, *add(label, protocol, address, params, timeout=1 if label == "HTTP deadline" else 5), expected, key)
              for label, protocol, address, params, expected, key in suite]
    if args.snmp:
        direct_cases = (*snmp_cases, (
            "SNMPv3 noAuthNoPriv via DNS", "NOAUTH", "3", "get", "1.3.6.1.2.1.1.3.0",
            {"security_level": "noAuthNoPriv"}, "1.3.6.1.2.1.1.3.0",
        ))
        for name, credential, version, operation, oid, extra, key in direct_cases:
            credentials = {"community": community}
            if credential == "NOAUTH":
                credentials = {"username": "zdsNoAuth"}
            elif version == "3":
                credentials = {"username": "zdsPriv" if credential == "PRIV" else "zdsAuth",
                               "auth_password": auth_password}
                if credential == "PRIV":
                    credentials["priv_password"] = privacy_password
            if operation == "getbulk":
                extra = {**extra, "max_repetitions": 4}
            device = post("devices", {
                "name": f"{name} (stored credentials)", "ip": "127.0.0.1", "initial_check_types": [],
                **({"hostname": "localhost"} if credential == "NOAUTH" else {}),
                "initial_snmp": {
                    "interval_seconds": 5, "credentials": credentials,
                    "connect_to": "dns" if credential == "NOAUTH" else "ip",
                    "params": {"version": version, "operation": operation, "oid": oid, "port": snmp_port, **extra},
                },
            }, admin)
            check = get(f"checks?device_id={device['id']}")[0]
            checks.append((device["name"], device["id"], check["id"], "ok", "snmp." + key))
    probe = launch("collector", [str(collector_binary), "--server-url", base, "--buffer-dir", str(work / "spool"),
                                 "--poll-tasks-interval-secs", "2", "--flush-interval-secs", "1"])
    failures = []
    for name, did, cid, expected, key in checks:
        wait_for(lambda: state(cid) not in ("unknown", None))
        observed = state(cid)
        if observed == expected:
            try:
                records = wait_for(lambda: samples(did, cid, key), seconds=10)
            except TimeoutError:
                records = []
        else:
            records = samples(did, cid, key)
        log("probe", label=name, state=observed, metric=key, value=records[-1]["value"] if records else None)
        if not (observed == expected and records):
            reason = next((sample.get("tags", {}).get("reason") for sample in get(f"metrics/latest?device_id={did}")
                           if sample["metric_key"] == "snmp.available"), None)
            log("probe_assertion_failed", label=name, reason=reason)
            failures.append(name)
    assert not failures, failures

    _, did, cid, _, _ = checks[0]
    service_available = False
    offline = time.monotonic()
    wait_for(lambda: state(cid) == "critical")
    wait_for(lambda: get(f"alerts?device_id={did}&status=active"))
    log("target_http_503", seconds=round(time.monotonic() - offline, 2))
    service_available = True
    recovery = time.monotonic()
    wait_for(lambda: state(cid) == "ok")
    wait_for(lambda: get(f"alerts?device_id={did}&status=resolved"))
    log("target_recovered", seconds=round(time.monotonic() - recovery, 2))

    _, tcp_device, tcp_check, _, _ = next(item for item in checks if item[0] == "TCP loopback")
    try:
        tcp.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass  # Windows rejects shutdown on a listening socket.
    tcp.close()
    wait_for(lambda: state(tcp_check) == "critical")
    log("tcp_listener_stopped", status=get(f"devices/{tcp_device}")["status"])


    disconnected = dt.datetime.now(dt.timezone.utc)
    service.terminate()
    service.wait(timeout=10)
    wait_for(lambda: list((work / "spool").glob("*.json")))
    queued = list((work / "spool").glob("*.json"))
    log("core_killed_spool_created", batches=len(queued))
    core("core-restart")
    wait_for(lambda: client.get("/readyz").is_success)
    wait_for(lambda: samples(did, cid, "http.available", disconnected))
    wait_for(lambda: not list((work / "spool").glob("*.json")))
    replayed = samples(did, cid, "http.available", disconnected)
    assert len({point["timestamp"] for point in replayed}) == len(replayed)
    log("core_restarted_spool_drained", samples_since_disconnect=len(replayed))
    assert probe.poll() is None
finally:
    for proc in reversed(processes):
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=12)
    for server in servers:
        server.shutdown()
        server.server_close()
    for stream in logs:
        stream.close()
    for name in ("self-signed.key", "expired.key"):
        (work / name).unlink(missing_ok=True)
    if args.snmp:
        for candidate in work.glob("*.conf*"):
            if candidate.is_file():
                candidate.unlink()  # Net-SNMP persistent files hold lab-only keys.
    client.close()
