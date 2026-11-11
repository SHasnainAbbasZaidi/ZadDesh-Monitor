from __future__ import annotations

import os
import secrets
from zaddesh_core.config import settings
import datetime
import random
from sqlalchemy.orm import Session
from zaddesh_core.database import SessionLocal
from zaddesh_core.models import (
    User,
    Role,
    Site,
    Group,
    Device,
    CheckTemplate,
    Check,
    CheckState,
    MetricRaw,
    Alert,
    Event,
    Collector,
)
from zaddesh_core.security import hash_password, hash_token


def seed_database():
    if not settings.demo_mode or not settings.database.url.startswith("sqlite:///") or not settings.database.url.endswith("zaddesh-demo.db"):
        raise SystemExit("Use ZADDESH_DEMO_MODE=true and a separate SQLite file named zaddesh-demo.db")
    password = os.getenv("ZADDESH_DEMO_PASSWORD", "")
    if len(password) < 16:
        raise SystemExit("Set ZADDESH_DEMO_PASSWORD to a unique password of at least 16 characters")
    db: Session = SessionLocal()

    try:
        user_count = db.query(User).count()
        if user_count > 0:
            print("Demo database already contains data; nothing was changed.")
            return


        print("Seeding ZadDesh demo dataset...")

        # 1. Roles
        roles = [
            Role(name="admin", description="Full administrative access", permissions=["*"]),
            Role(name="operator", description="Read and write monitoring configurations", permissions=["devices:*", "checks:*", "alerts:*"]),
            Role(name="viewer", description="Read-only visibility into dashboards and devices", permissions=["read:*"]),
        ]
        db.add_all(roles)
        db.flush()

        admin_role = next(r for r in roles if r.name == "admin")

        # 2. Users (Admin + Demo Operator)
        admin_user = User(
            username="admin",
            email="admin@zaddesh.internal",
            hashed_password=hash_password(password),
            full_name="ZadDesh Administrator",
            role="admin",
            role_id=admin_role.id,
            is_active=True,
        )
        db.add(admin_user)

        operator_user = User(
            username="operator",
            email="ops@zaddesh.internal",
            hashed_password=hash_password(secrets.token_urlsafe(32)),
            full_name="NetOps Lead",
            role="operator",
            role_id=next(r for r in roles if r.name == "operator").id,
            is_active=True,
        )
        db.add(operator_user)
        db.flush()

        # 3. Sites
        sites = [
            Site(name="Primary Datacenter (DC-1)", slug="dc-1", description="Equinix Ashburn Core Facility", address="21711 Filigree Ct, Ashburn, VA"),
            Site(name="Disaster Recovery (DR-2)", slug="dr-2", description="CyrusOne Austin Recovery Site", address="Metropolis Dr, Austin, TX"),
            Site(name="London Edge POP", slug="lon-edge", description="Telehouse Europe Docklands", address="Coriander Ave, London, UK"),
        ]
        db.add_all(sites)
        db.flush()

        # 4. Groups
        groups = [
            Group(name="Core Infrastructure", slug="core-infra", description="Core switches, routers, firewalls", tags=["network", "tier1"]),
            Group(name="Kubernetes & Compute", slug="k8s-compute", description="Bare-metal and VM hypervisors", tags=["compute", "linux"]),
            Group(name="Database Cluster", slug="databases", description="PostgreSQL and Redis clusters", tags=["database", "storage"]),
            Group(name="External Endpoints", slug="external-web", description="Public web apps and API endpoints", tags=["web", "public"]),
        ]
        db.add_all(groups)
        db.flush()

        # 5. Check Templates
        templates = [
            CheckTemplate(name="Standard ICMP Ping", check_type="icmp", description="Echo request 3 packets", default_params={"count": 3, "timeout_ms": 1000}, default_interval=30),
            CheckTemplate(name="HTTPS Web Probe", check_type="http", description="HTTP GET assertion 200 OK with TLS verify", default_params={"method": "GET", "expected_code": 200, "tls_check": True}, default_interval=60),
            CheckTemplate(name="Database Port TCP", check_type="tcp", description="TCP handshake on standard DB port", default_params={"timeout_ms": 2000}, default_interval=30),
        ]
        db.add_all(templates)
        db.flush()

        # 6. Devices
        dev_defs = [
            ("core-sw01.dc1", "10.0.0.1", "Cisco Catalyst 9500 Core Switch", sites[0].id, groups[0].id, "up", "Cisco IOS-XE", "Cisco", "Catalyst 9500"),
            ("edge-rtr01.dc1", "10.0.0.2", "Juniper MX240 Border Gateway", sites[0].id, groups[0].id, "up", "Junos OS 22.4", "Juniper", "MX240"),
            ("fw-cluster.dc1", "10.0.0.5", "FortiGate 600E HA Cluster", sites[0].id, groups[0].id, "up", "FortiOS 7.2", "Fortinet", "FortiGate 600E"),
            ("k8s-master-01", "10.10.1.10", "Kubernetes Control Plane 01", sites[0].id, groups[1].id, "up", "Ubuntu 22.04 LTS", "Dell", "PowerEdge R650"),
            ("k8s-worker-01", "10.10.1.21", "Kubernetes Worker Node 01", sites[0].id, groups[1].id, "up", "Ubuntu 22.04 LTS", "Dell", "PowerEdge R750"),
            ("k8s-worker-02", "10.10.1.22", "Kubernetes Worker Node 02", sites[0].id, groups[1].id, "warning", "Ubuntu 22.04 LTS", "Dell", "PowerEdge R750"),
            ("pg-primary.dc1", "10.20.1.5", "PostgreSQL 16 Primary Cluster", sites[0].id, groups[2].id, "up", "Debian 12 Bookworm", "Supermicro", "SYS-1029P"),
            ("redis-cluster.dc1", "10.20.1.12", "Redis In-Memory Cache Primary", sites[0].id, groups[2].id, "up", "Debian 12 Bookworm", "Supermicro", "SYS-1029P"),
            ("lon-gw01.edge", "195.66.224.1", "London Edge MikroTik CCR2116", sites[2].id, groups[0].id, "warning", "RouterOS v7", "MikroTik", "CCR2116"),
            ("api.zaddesh.example", "192.0.2.22", "ZadDesh Public API Gateway", sites[0].id, groups[3].id, "up", "Linux Envoy", "Cloudflare/Edge", "Proxy"),
            ("portal.zaddesh.example", "192.0.2.23", "ZadDesh Web Portal", sites[0].id, groups[3].id, "up", "Linux Nginx", "Cloudflare/Edge", "Proxy"),
            ("backup-nas.dr2", "10.50.0.100", "TrueNAS CORE Backup Target", sites[1].id, groups[1].id, "down", "TrueNAS 13.0", "iXsystems", "TrueNAS M50"),
        ]

        devices = []
        for name, ip, hostname, s_id, g_id, st, os_n, vendor, model in dev_defs:
            uptime = random.randint(86400 * 5, 86400 * 180) if st != "down" else 0
            d = Device(
                name=name,
                ip=ip,
                hostname=hostname,
                site_id=s_id,
                group_id=g_id,
                status=st,
                os=os_n,
                vendor=vendor,
                model=model,
                uptime_seconds=uptime,
                last_seen=datetime.datetime.utcnow() if st != "down" else (datetime.datetime.utcnow() - datetime.timedelta(hours=3)),
                is_monitored=True,
            )
            db.add(d)
            devices.append(d)
        db.flush()

        # 7. Add Checks & CheckStates for each device
        checks = []
        for d in devices:
            # ICMP Ping check
            c_icmp = Check(
                device_id=d.id,
                name=f"{d.name} Ping",
                check_type="icmp",
                target=d.ip,
                params={"count": 3, "timeout": 2000},
                interval_seconds=30,
                timeout_seconds=5,
                retries=3,
                is_enabled=True,
            )
            db.add(c_icmp)
            checks.append(c_icmp)
            db.flush()

            icmp_status = "ok" if d.status == "up" else ("warning" if d.status == "warning" else "critical")
            db.add(
                CheckState(
                    check_id=c_icmp.id,
                    current_status=icmp_status,
                    last_check_time=datetime.datetime.utcnow(),
                    last_change_time=datetime.datetime.utcnow() - datetime.timedelta(hours=2),
                    consecutive_failures=0 if icmp_status == "ok" else 4,
                    flap_detected=False,
                    message="3 packets transmitted, 3 received, 0% packet loss" if icmp_status == "ok" else "Packet loss detected",
                )
            )

            # Extra checks based on role
            if "pg-primary" in d.name:
                c_tcp = Check(
                    device_id=d.id,
                    name="PostgreSQL Port 5432",
                    check_type="tcp",
                    target=f"{d.ip}:5432",
                    params={"port": 5432},
                    interval_seconds=30,
                    timeout_seconds=5,
                    retries=3,
                    is_enabled=True,
                )
                db.add(c_tcp)
                checks.append(c_tcp)
                db.flush()
                db.add(CheckState(check_id=c_tcp.id, current_status="ok", last_check_time=datetime.datetime.utcnow(), consecutive_failures=0, flap_detected=False, message="TCP 5432 connected in 1.4ms"))

            if "api.zaddesh" in d.name or "portal.zaddesh" in d.name:
                c_http = Check(
                    device_id=d.id,
                    name="HTTPS Health Probe",
                    check_type="http",
                    target=f"https://{d.name}/healthz",
                    params={"expected_status": 200},
                    interval_seconds=60,
                    timeout_seconds=5,
                    retries=3,
                    is_enabled=True,
                )
                db.add(c_http)
                checks.append(c_http)
                db.flush()
                db.add(CheckState(check_id=c_http.id, current_status="ok", last_check_time=datetime.datetime.utcnow(), consecutive_failures=0, flap_detected=False, message="HTTP 200 OK (38ms, TLS valid 74d)"))

        db.flush()

        # 8. Local Collector Registration
        collector = Collector(
            name="collector-local-01",
            auth_token_hash=hash_token(secrets.token_hex(32)),
            site_id=sites[0].id,
            version="0.1.0",
            hostname="zaddesh-master-node",
            ip_address="127.0.0.1",
            status="online",
            last_heartbeat_at=datetime.datetime.utcnow(),
            registered_at=datetime.datetime.utcnow(),
        )
        db.add(collector)

        # 9. Seed Realistic Metrics for the last 60 minutes
        print("Generating historical metric series...")
        raw_metrics = []
        now = datetime.datetime.utcnow()

        for d in devices:
            base_rtt = random.uniform(1.2, 12.0)
            if d.status == "warning":
                base_rtt = 88.0

            # 30 intervals of 2 minutes = 60 minutes
            for step in range(30, 0, -1):
                ts = now - datetime.timedelta(minutes=step * 2)

                if d.status == "down":
                    val_rtt = 0.0
                    val_avail = 0.0
                elif d.status == "warning":
                    val_rtt = base_rtt + random.uniform(-10.0, 35.0)
                    val_avail = 0.95
                else:
                    val_rtt = base_rtt + random.uniform(-0.4, 0.8)
                    val_avail = 1.0

                raw_metrics.append(
                    MetricRaw(
                        device_id=d.id,
                        metric_key="icmp.rtt_avg",
                        timestamp=ts,
                        value=round(max(0.1, val_rtt), 2),
                        tags={"unit": "ms"},
                    )
                )
                raw_metrics.append(
                    MetricRaw(
                        device_id=d.id,
                        metric_key="icmp.availability",
                        timestamp=ts,
                        value=val_avail,
                        tags={"unit": "ratio"},
                    )
                )

                # CPU utilization for servers and switches
                cpu_load = random.uniform(15.0, 45.0)
                if d.name == "k8s-worker-02":
                    cpu_load = random.uniform(82.0, 96.0)
                raw_metrics.append(
                    MetricRaw(
                        device_id=d.id,
                        metric_key="cpu.utilization",
                        timestamp=ts,
                        value=round(cpu_load, 1),
                        tags={"unit": "percent"},
                    )
                )

        db.bulk_save_objects(raw_metrics)

        # 10. Seed Alerts & Events
        down_dev = next(d for d in devices if d.status == "down")
        warn_dev = next(d for d in devices if d.name == "k8s-worker-02")
        lon_dev = next(d for d in devices if d.name == "lon-gw01.edge")

        alerts = [
            Alert(
                device_id=down_dev.id,
                severity="critical",
                status="active",
                title=f"Host Unreachable: {down_dev.name}",
                message=f"Target {down_dev.ip} is unresponsive to ICMP probes for > 15 minutes.",
                value=0.0,
                triggered_at=now - datetime.timedelta(minutes=45),
            ),
            Alert(
                device_id=warn_dev.id,
                severity="high",
                status="active",
                title="High CPU Utilization (> 90%)",
                message=f"Node {warn_dev.name} average CPU utilization exceeded 90% threshold for 10 minutes.",
                value=94.2,
                triggered_at=now - datetime.timedelta(minutes=22),
            ),
            Alert(
                device_id=lon_dev.id,
                severity="warning",
                status="acknowledged",
                title="Elevated RTT Latency Spike",
                message="Average ICMP round-trip time exceeded 75ms on London edge uplink.",
                value=88.5,
                triggered_at=now - datetime.timedelta(minutes=50),
            ),
        ]
        db.add_all(alerts)

        events = [
            Event(
                device_id=down_dev.id,
                event_type="device_down",
                severity="critical",
                title=f"Device {down_dev.name} went DOWN",
                message=f"Consecutive ping probe failures on {down_dev.ip}",
                details={"status": "down"},
                timestamp=now - datetime.timedelta(minutes=45),
            ),
            Event(
                device_id=warn_dev.id,
                event_type="threshold_exceeded",
                severity="warning",
                title="CPU Utilization Warning",
                message="CPU exceeded 85% utilization threshold",
                details={"metric": "cpu.utilization", "val": 94.2},
                timestamp=now - datetime.timedelta(minutes=22),
            ),
            Event(
                device_id=devices[0].id,
                event_type="system_startup",
                severity="info",
                title="ZadDesh Collector Started",
                message="Collector engine registered and healthy",
                details={"version": "0.1.0"},
                timestamp=now - datetime.timedelta(hours=1),
            ),
        ]
        db.add_all(events)

        db.commit()
        print("Database seeded successfully!")

    except Exception as e:
        db.rollback()
        print(f"Error seeding database: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_database()
