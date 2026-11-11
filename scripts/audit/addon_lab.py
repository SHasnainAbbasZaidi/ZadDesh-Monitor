"""Run real HTTP and Net-SNMP add-on probes in a disposable Linux lab."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

ROOT = Path(__file__).resolve().parents[2]


def free_port(kind):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, limit=30):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        try:
            result = predicate()
            if result:
                return result
        except httpx.TransportError:
            pass
        time.sleep(.25)
    raise TimeoutError("An acceptance condition did not become true")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collector", type=Path, required=True)
    parser.add_argument("--topology-only", action="store_true", help="Skip the HTTP and webhook acceptance checks")
    args = parser.parse_args()
    binary = args.collector.resolve(strict=True)
    http_status = 200
    deliveries = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(http_status)
            self.end_headers()
            self.wfile.write(b"Add-on test target")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            deliveries.append(body["event"])
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    processes = []
    try:
        with tempfile.TemporaryDirectory(prefix="zaddesh-addon-") as directory:
            work = Path(directory)
            core_port = free_port(socket.SOCK_STREAM)
            snmp_port = free_port(socket.SOCK_DGRAM)
            auth, privacy, bootstrap, collector_token = (secrets.token_hex(24) for _ in range(4))
            env = dict(os.environ, ZADDESH_DATABASE__URL="sqlite:///" + (work / "lab.db").as_posix(),
                       ZADDESH_SECURITY__ENCRYPTION_KEY=secrets.token_hex(32),
                       ZADDESH_FEATURES__INCIDENT_WORKSPACE="true", ZADDESH_FEATURES__NETWORK_DIAGRAM_BETA="true",
                       ZADDESH_FEATURES__TOPOLOGY_ALLOWED_RANGES='["127.0.0.0/8"]',
                       ZADDESH_FEATURES__INCIDENT_WEBHOOK_URL=f"http://127.0.0.1:{server.server_port}/webhook",
                       ZADDESH_COLLECTOR__ENROLLMENT_TOKEN=bootstrap, ZADDESH_COLLECTOR_ENROLLMENT_TOKEN=bootstrap,
                       ZADDESH_COLLECTOR_TOKEN=collector_token, ZADDESH_COLLECTOR_NETWORK_DIAGRAM_BETA="true",
                       ZADDESH_DEMO_MODE="false", SNMP_PERSISTENT_DIR=str(work), MIBS="")
            # Expose a controlled LLDP neighbor in the real SNMP agent.
            fixture = work / "lldp"
            fixture.write_text("""#!/usr/bin/env python3
import sys
entries = {
 '.1.0.8802.1.1.2.1.3.7.1.3.1': 'lo',
 '.1.0.8802.1.1.2.1.4.1.1.5.0.1.1': 'fixture-chassis',
 '.1.0.8802.1.1.2.1.4.1.1.7.0.1.1': 'Gi0/2',
 '.1.0.8802.1.1.2.1.4.1.1.9.0.1.1': 'fixture-neighbor',
}
order = lambda oid: tuple(map(int, oid.strip('.').split('.')))
query = sys.argv[2]
if sys.argv[1] == '-n':
    query = next((item for item in sorted(entries, key=order) if order(item) > order(query)), '')
if query in entries:
    print(query, 'string', entries[query], sep='\\n')
""")
            fixture.chmod(0o700)
            conf = work / "agent.conf"
            conf.touch(mode=0o600)
            conf.write_text(f"""agentAddress udp:127.0.0.1:{snmp_port}
createUser addonLab SHA {auth} AES {privacy}
rouser addonLab priv
sysName fixture-seed
pass .1.0.8802.1.1.2 {fixture}
""")
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=ROOT / "core", env=env, check=True)
            with (work / "agent.log").open("wb") as agent_log, (work / "core.log").open("wb") as core_log, (work / "collector.log").open("wb") as collector_log:
                processes.append(subprocess.Popen(["snmpd", "-f", "-Lo", "-C", "-c", str(conf)], env=env,
                                                  stdout=agent_log, stderr=agent_log))
                processes.append(subprocess.Popen([sys.executable, "-m", "uvicorn", "zaddesh_core.main:app", "--host",
                                                   "127.0.0.1", "--port", str(core_port), "--no-proxy-headers"],
                                                  cwd=ROOT / "core", env=env, stdout=core_log, stderr=core_log))
                base = f"http://127.0.0.1:{core_port}"
                with httpx.Client(base_url=base, timeout=6, trust_env=False) as client:
                    wait_for(lambda: client.get("/readyz").is_success)
                    session = client.post("/api/v1/auth/setup", json={"username": "labadmin", "email": "lab@example.com",
                                                                      "password": secrets.token_hex(24)}).raise_for_status().json()
                    client.headers["Authorization"] = "Bearer " + session["access_token"]

                    def post(route, body):
                        return client.post("/api/v1/" + route, json=body).raise_for_status().json()

                    def get(route):
                        return client.get("/api/v1/" + route).raise_for_status().json()

                    if not args.topology_only:
                        target = post("devices", {"name": "HTTP fixture", "ip": "127.0.0.1", "initial_check_types": []})
                        check = post("checks", {"device_id": target["id"], "name": "Fixture HTTP", "check_type": "http",
                                                "target": f"http://127.0.0.1:{server.server_port}", "interval_seconds": 5,
                                                "timeout_seconds": 2, "retries": 0})
                    for label, secret in (("fixture-seed", auth), ("wrong-credential", secrets.token_hex(24))):
                        post("devices", {"name": label, "ip": "127.0.0.1", "initial_check_types": [],
                            "initial_snmp": {"params": {"version": "3", "port": snmp_port, "security_level": "authPriv",
                                                        "auth_protocol": "sha1", "privacy_protocol": "aes128"},
                                             "credentials": {"username": "addonLab", "auth_password": secret,
                                                             "priv_password": privacy}}})
                    processes.append(subprocess.Popen([str(binary), "--server-url", base, "--buffer-dir", str(work / "spool"),
                                                       "--poll-tasks-interval-secs", "2", "--flush-interval-secs", "1"],
                                                      env=env, stdout=collector_log, stderr=collector_log))
                    if not args.topology_only:
                        wait_for(lambda: get(f"checks/{check['id']}")["state"]["current_status"] == "ok")
                        started = time.monotonic()
                        http_status = 503
                        incident_route = f"incidents/alerts?device_id={target['id']}"
                        opened = wait_for(lambda: get(incident_route)["alerts"])
                        assert len(opened) == 1 and opened[0]["status"] == "active"
                        down_seconds = time.monotonic() - started
                        started = time.monotonic()
                        http_status = 200
                        wait_for(lambda: get(incident_route)["alerts"][0]["status"] == "resolved")
                        up_seconds = time.monotonic() - started
                        closed = get(incident_route)["alerts"]
                        assert len(closed) == 1 and closed[0]["id"] == opened[0]["id"]
                        try:
                            wait_for(lambda: {"opened", "resolved"} <= set(deliveries))
                        except TimeoutError:
                            attempts = get(f"incidents/alerts/{opened[0]['id']}")["notifications"]
                            print(json.dumps({"received": deliveries, "attempts": attempts}), flush=True)
                            raise
                        print(json.dumps({"http_open_seconds": round(down_seconds, 2), "http_resolve_seconds": round(up_seconds, 2),
                                          "same_alert": True, "deliveries": deliveries}), flush=True)

                    diagram = wait_for(lambda: next((item["id"] for item in get("network-diagram") if item["automatic"]), None))

                    def finished():
                        runs = get(f"network-diagram/{diagram}/discovery")
                        return runs if len(runs) == 2 and all(item["status"] in {"complete", "failed"} for item in runs) else None

                    runs = wait_for(finished, limit=65)
                    graph = get(f"network-diagram/{diagram}")
                    print(json.dumps({"automatic": graph["automatic"], "snmp_runs": runs,
                                      "nodes": len(graph["nodes"]), "links": len(graph["links"])}), flush=True)
                    assert {item["status"] for item in runs} == {"complete", "failed"}
                    assert len(graph["nodes"]) == 2 and len(graph["links"]) == 1
                    assert graph["links"][0]["confidence"] == "high" and graph["links"][0]["method"] == "lldp"
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=15)
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
