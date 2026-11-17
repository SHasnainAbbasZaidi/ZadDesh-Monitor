from zaddesh_core.api.v1.infrastructure_monitoring import summarize
from zaddesh_core.infrastructure_monitoring import PENALTIES, classify, health_score, item_kind, port_utilization


def test_category_precedence_and_units():
    assert classify({"name": "Windows recorder", "tags": [{"tag": "role", "value": "NVR"}]}) == "CCTV"
    assert classify({"name": "appliance", "parentTemplates": [{"name": "FortiGate by SNMP"}]}) == "Firewalls"
    assert classify({"name": "Linux host", "hostgroups": [{"name": "Routers"}]}) == "Routers"
    assert item_kind({"key_": "system.cpu.util[,user]", "units": "%"}) is None
    assert item_kind({"key_": "net.if.in[eth0]", "units": "B"}) is None
    assert port_utilization(700_000_000, 800_000_000, 1_000_000_000) == 80


def test_health_is_coverage_aware_deduplicates_and_respects_maintenance():
    problems = [{"title": "Switch fan failure", "severity": 4, "suppressed": False},
                {"title": "Fan failure detected", "severity": 5, "suppressed": False},
                {"title": "Uplink link down", "severity": 4, "suppressed": True}]
    score, penalties = health_score("online", False, True, problems, ["hardware"], False, None, PENALTIES, True)
    assert score == 65 and len(penalties) == 1
    assert health_score("online", False, True, [], [], False, None, PENALTIES, True)[0] is None
    assert health_score("offline", False, False, [], [], False, None, PENALTIES, False)[0] == 0
    assert health_score("offline", True, True, [], [], False, None, PENALTIES, True)[0] is None


def test_summary_rolls_measurements_and_uses_only_confirmed_outages(db_session):
    now = 1_800_000_000

    def item(item_id, host, key, value, units, age=20):
        return {"itemid": str(item_id), "hostid": str(host), "key_": key, "name": key, "lastvalue": str(value),
                "units": units, "lastclock": str(now - age), "value_type": "0" if units == "%" else "3", "delay": "60s"}

    class Zabbix:
        version, token, url = "7.4.1", "test-api-key", "http://192.0.2.3"

        def rows(self, method, params):
            if method == "host.get":
                return [{"hostid": str(host), "host": f"Windows host {host}", "status": "0", "active_available": "1"}
                        for host in (1, 2, 3)]
            if method == "item.get":
                return [item(1, 1, "system.cpu.util", 95, "%"), item(2, 1, "vm.memory.utilization", 7, "%", age=800),
                        item(3, 1, "icmpping", 1, ""), item(4, 2, "icmpping", 0, ""), item(5, 3, "icmpping", 0, "", age=600)]
            if method == "history.get":
                samples = {"1": [(900, 30), (200, 95)], "4": [(60, 0), (120, 0), (180, 0)]}
                return [{"itemid": item_id, "value": str(value), "clock": str(now - age)}
                        for item_id in params["itemids"] for age, value in samples.get(item_id, [])]
            assert method == "problem.get"
            return []

    summary, _ = summarize(Zabbix(), db_session, now)
    first, offline, stale = summary["devices"]
    assert first["resources"] == {"cpu": {"current": 95, "value": 62.5, "samples": 2}}
    assert first["health"] == 100
    assert (offline["status"], offline["health"]) == ("offline", 0)
    assert (stale["status"], stale["health"]) == ("unknown", None)


def test_detail_keeps_admin_permission(client, viewer_headers):
    assert client.get("/api/v1/infrastructure-monitoring/1", headers=viewer_headers).status_code == 403
