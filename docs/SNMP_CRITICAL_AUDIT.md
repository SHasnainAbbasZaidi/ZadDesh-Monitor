# SNMP Critical Errors, Priority Alerts and Notifications Audit

Date: 2026-11-18. Scope: the supplied 127 device/error requirements. Existing Rust collector, FastAPI ingestion, Incident/Alert records and email worker were reused. No UI files, database models, migrations, device configuration, SNMP credentials, user roles, or saved thresholds were changed. SNMP requests are read-only; no agent installation or SNMP SET commands were performed.

## Findings and changes

Baseline: 24 incident/email/topology tests passed before edits. These tests exercised existing functionality but did not prove that every requested SNMP rule existed.

1. AlertRule records existed without a native evaluator. Added evaluation of explicitly configured scalar threshold and counter-rate rules in the existing ingestion transaction. Existing operators, thresholds, durations, severity, enablement and device priority are honored. Duplicate equivalent rules, repeated samples, old/replayed observations and counter resets do not generate repeated incidents or artificial rate spikes. Duration evaluation requires continuous observations and is bounded to 2,000 samples; oversized windows remain unevaluated rather than inventing a result.
2. Added eight documented agent-state alarm families: Cisco voltage, temperature, fan and power-supply critical states, plus Net-SNMP configured disk-space, CPU-load, process-count and swap-space error flags. No utilization/temperature thresholds were invented. Cisco warning, absent and nonfunctioning-monitor states are not treated as proof of critical hardware failure or recovery.
3. Added optional, enterprise-selected alarm reads after existing topology/workstation discovery. Existing 55-second, 512-values-per-root and 8,000-total-value bounds remain. Unsupported optional subtrees do not fail an otherwise successful inventory. A partial/time-limited scan is not proof that absent alarms are healthy. Existing snapshots now pass collected alarm values and explicitly configured scalar values to the same evaluator and metric store.
4. SNMP communication failures now produce the short warning title **SNMP Unreachable**, with an explanation that device outage is unconfirmed. Unsupported/non-numeric objects produce `snmp.supported=0` and an unknown check state, rather than a healthy or failed device-availability sample. Native SNMP failures no longer independently establish device-offline status.
5. Native incidents were not connected to SMTP: the worker required a Zabbix connection. Connected native episodes to the existing recipients, filters, templates, durable email queue, retry policy, quiet hours, reminders/escalations and recovery routing. Queue intake is bounded to 200 native episodes per cycle and favors unobserved/least-recently-observed episodes. Enabling routing does not send an old-alert backlog. Host-group, host and port filters are not broadened to match unknown native scopes; Zabbix-specific port selections still require matching Zabbix context.
6. Critical Errors omitted hardware/VPN/other important upstream faults. High/critical impact-bearing faults now enter the existing Critical view without adding UI categories or changing layout. Existing priority filtering remains. Generic access-port link-down remains outside that view. Native server/10G-or-faster/explicit uplink failures are critical; ordinary interfaces are warning. Known expected speed is retained when a down interface reports zero speed.

Short native titles include Fan Failure, Power Supply Failure, Temperature Critical, Voltage Critical, Disk Space Critical, High CPU Load, Process Count Critical, Swap Space Critical, Uplink Down and SNMP Unreachable. Messages include device name, IP, check/component OID, actual value and applicable configured condition. Existing custom rule names and upstream trigger text are preserved; the existing expandable UI supplies compact display titles.

## Evidence codes for the complete matrix

- **F-COMM:** communication detection/lifecycle existed and was corrected; SNMP timeout is a monitoring warning, not device-offline proof.
- **E-LINK:** existing interface observation, incident correlation, recovery and flap tracking; corrected impact/severity. Limited to discovered/observed links. Unmanaged access ports are not automatically critical.
- **A-ENV:** newly added Cisco ENVMON state rule and conditional read. Requires the model/agent to implement this MIB; unavailable sensors are not healthy. Other manufacturers need their own verified MIB.
- **A-UCD:** newly added Net-SNMP alarm flag. Uses the agent's already-configured limits. CPU load is not CPU usage percentage; process-count alarms are not a generic service-health proof.
- **C-THRESH:** native scalar threshold evaluator is now functional for an explicitly configured metric/operator/duration. Requires a correctly supported OID, units and policy. No matching default policy was silently created.
- **C-RATE:** native configured counter-rate evaluation is now functional. Requires a valid baseline, cadence and explicit rate threshold. Reset/wrap intervals are unknown; no synthetic wrap rate is invented.
- **U:** no reliable built-in native detector/adapter established for this requirement. An existing, correctly configured Zabbix trigger may report it, but upstream ingestion is not native detection or verification of the physical fault. Unsupported items are not reported as passed.

Common prerequisite for all alerting: priority enabled. The matrix describes implementation capability, not certification on physical equipment in every category. Only existing live devices were inspected; cross-category policy tests are synthetic fixtures.

### Cisco network switches — 20 requirements

| Requested error | Result | Capability or limitation |
|---|---|---|
| Switch SNMP Unreachable | F-COMM | Communication warning, not switch-down proof |
| Critical Interface Down | E-LINK | Observed server/critical uplinks; not every user port |
| Uplink Down | E-LINK | Expected/current speed, server endpoints and explicit uplink names |
| Port Flapping | E-LINK | Existing topology incident failure-transition tracking |
| High Bandwidth Utilization | C-THRESH | Requires utilization metric/policy; octet counters alone are not utilization |
| Excessive CRC / Interface Errors | C-RATE | Supported error counter; CRC-specific OID depends on platform |
| Packet Drops / Discards | C-RATE | IF-MIB counters and configured per-second policy |
| Unexpected Reboot | U | Agent uptime reset alone cannot prove unexpected chassis reboot |
| High CPU Usage | C-THRESH | Supported CPU percentage OID/policy |
| High Memory Usage | C-THRESH | Supported memory metric/policy; used bytes alone are not a percentage |
| High Temperature | A-ENV | ENVMON temperature state, not invented Celsius threshold |
| Fan Failure | A-ENV | ENVMON fan critical/shutdown state |
| Power Supply Failure | A-ENV | ENVMON supply critical/shutdown state |
| SFP / Transceiver Failure | U | Needs supported transceiver/DOM fault MIB and expected optic |
| Interface Err-Disabled | U | ifOperStatus=down cannot prove err-disable cause |
| Excessive STP Topology Changes | C-RATE | Explicit STP counter policy; reset interval unknown |
| Network Loop Indicators | U | STP change counts are not conclusive loop detection |
| Broadcast Storm Indicators | C-RATE | Supported broadcast counter and explicit rate policy only |
| EtherChannel Member Failure | U | Requires LAG membership/expected-member and operational-state correlation |
| Hardware Module Failure | U | Requires vendor module/FRU state mapping, not generic sensor guessing |

### Windows servers — 13 requirements

| Requested error | Result | Capability or limitation |
|---|---|---|
| Server SNMP Unreachable | F-COMM | Agent/transport warning |
| High CPU Usage | C-THRESH | HOST-RESOURCES CPU load or verified vendor CPU metric |
| High Memory Usage | C-THRESH | Explicit supported metric/threshold; storage rows need correct type/denominator |
| Critical Disk Space | C-THRESH | Explicit utilization/free-space policy; no disk inference from unavailable data |
| Network Interface Down | E-LINK | Observed server link; unobserved NICs need a configured check/upstream rule |
| High Bandwidth Utilization | C-THRESH | Supported normalized metric and policy |
| Network Interface Errors | C-RATE | Valid IF-MIB counter baseline |
| Packet Drops / Discards | C-RATE | Configured counter-rate policy |
| Unexpected Reboot | U | Agent restart and planned reboot must not be confused |
| Disk / RAID Hardware Failure | U | Requires supported iDRAC/iLO/controller/vendor MIB |
| Hardware Temperature Critical | U | Windows SNMP alone does not expose a universal temperature alarm |
| Power Supply Failure | U | Requires server hardware-management MIB |
| Fan Failure | U | Requires server hardware-management MIB |

### Linux servers — 15 requirements

| Requested error | Result | Capability or limitation |
|---|---|---|
| Server SNMP Unreachable | F-COMM | Agent/transport warning |
| High CPU Usage | C-THRESH / A-UCD | Percentage policy or separately labelled agent CPU-load alarm |
| High Memory Usage | C-THRESH | Supported metric and explicit policy |
| Critical Disk Space | A-UCD / C-THRESH | Agent-configured minimum-space flag or explicit scalar policy |
| Critical Swap Usage | A-UCD | Agent-configured minimum available swap flag |
| Network Interface Down | E-LINK | Observed server link |
| High Bandwidth Utilization | C-THRESH | Supported normalized metric/policy |
| Network Interface Errors | C-RATE | Supported counters and policy |
| Packet Drops / Discards | C-RATE | Supported counters and policy |
| Unexpected Reboot | U | Generic uptime reset is insufficient evidence |
| Disk / RAID Hardware Failure | U | Controller/private MIB required |
| Hardware Temperature Critical | U | Platform-specific sensor/alarm mapping required |
| Fan Failure | U | Platform-specific hardware-management MIB required |
| Power Supply Failure | U | Platform-specific hardware-management MIB required |
| Critical Process Failure | A-UCD | Only processes already watched by the agent's process-count policy |

### Network routers — 21 requirements

| Requested error | Result | Capability or limitation |
|---|---|---|
| Router SNMP Unreachable | F-COMM | Monitoring communication warning |
| WAN Interface Down | E-LINK | Discovered/explicit uplink; arbitrary WAN role is not inferred |
| LAN Interface Down | E-LINK | Observed links; critical impact must be established |
| Critical Uplink Down | E-LINK | Existing topology lifecycle, corrected severity |
| Interface Flapping | E-LINK | Existing topology failure-transition tracking |
| High Bandwidth Utilization | C-THRESH | Supported utilization metric/policy |
| Network Interface Errors | C-RATE | Explicit supported counter policy |
| Packet Drops / Discards | C-RATE | Explicit supported counter policy |
| Unexpected Reboot | U | Uptime reset does not establish unexpected reboot |
| High CPU Usage | C-THRESH | Supported OID and policy |
| High Memory Usage | C-THRESH | Supported OID and policy |
| High Temperature | A-ENV / U | Cisco ENVMON where supported; other platform mappings not established |
| Power Supply Failure | A-ENV / U | Cisco ENVMON where supported |
| Fan Failure | A-ENV / U | Cisco ENVMON where supported |
| BGP Neighbor Down | U | No automatic peer-intent/admin-state/established-baseline adapter |
| OSPF Neighbor Down | U | Two-way neighbors can be normal; no adjacency-role adapter |
| Default Route Missing | U | Requires expected default route/VRF policy |
| Routing Table Capacity Critical | C-THRESH | Only a supported explicit capacity metric/policy; no guessed capacity |
| VPN Tunnel Down | U | Requires supported VPN MIB and expected tunnel configuration |
| WAN Failover Failure | U | Requires expected failover-role/state correlation |
| SFP / Transceiver Failure | U | Vendor optic/DOM MIB and expected hardware required |

### Network firewalls — 29 requirements

| Requested error | Result | Capability or limitation |
|---|---|---|
| Firewall SNMP Unreachable | F-COMM | Monitoring communication warning |
| WAN Interface Down | E-LINK | Observed/explicit uplinks only |
| LAN Interface Down | E-LINK | Observed links with established impact |
| Interface Flapping | E-LINK | Existing topology failure-transition tracking |
| High Bandwidth Utilization | C-THRESH | Supported utilization metric and policy |
| Network Interface Errors | C-RATE | Explicit supported counter policy |
| Packet Drops / Discards | C-RATE | Explicit supported counter policy |
| Unexpected Reboot | U | Generic uptime reset is insufficient |
| High CPU Usage | C-THRESH | Supported CPU metric and policy |
| High Memory Usage | C-THRESH | Supported memory metric and policy |
| High Session Table Utilization | C-THRESH | Requires vendor capacity/utilization metric and policy |
| VPN Tunnel Down | U | Vendor MIB plus intended tunnel state required |
| SD-WAN Link Failure | U | Vendor state/role mapping required |
| SD-WAN Health Check Failure | U | Vendor health-check mapping required |
| SD-WAN Packet Loss Critical | C-THRESH | Only with supported vendor loss metric and explicit policy |
| SD-WAN Latency Critical | C-THRESH | Only with supported vendor latency metric/units/policy |
| SD-WAN Jitter Critical | C-THRESH | Only with supported vendor jitter metric/units/policy |
| HA Cluster Member Failure | U | Vendor cluster/member expectations required |
| HA Failover Event | U | Vendor trap/polled state plus role-change correlation required |
| HA Heartbeat Failure | U | Vendor heartbeat MIB required |
| High Temperature | A-ENV / U | Cisco ENVMON where supported; other sensor adapters not established |
| Power Supply Failure | A-ENV / U | Cisco ENVMON where supported |
| Fan Failure | A-ENV / U | Cisco ENVMON where supported |
| Log Storage Full | C-THRESH | Explicit supported log-storage capacity/free-space metric and policy |
| IPS Attack Detection | U | Vendor event MIB/traps/log integration; not generic SNMP |
| Antivirus Malware Detection | U | Vendor event MIB/traps/log integration required |
| DHCP Pool Exhaustion | C-THRESH | Supported free-address/capacity metric and explicit policy |
| BGP Neighbor Failure | U | No automatic administrative/established-baseline peer adapter |
| OSPF Neighbor Failure | U | No automatic adjacency-role adapter |

### CCTV / IP cameras — 16 requirements

| Requested error | Result | Capability or limitation |
|---|---|---|
| Camera SNMP Unreachable | F-COMM | No inference that camera power/video failed |
| Unexpected Camera Reboot | U | Uptime reset alone insufficient |
| Network Interface Errors | C-RATE | Only if IF-MIB counters supported and configured |
| High Bandwidth Usage | C-THRESH / C-RATE | Explicit supported utilization/rate metric and policy |
| Camera Overheating | U | Verified camera temperature/alarm MIB required |
| Camera Tampering Detected | U | Vendor event/trap mapping required |
| Camera Power Failure | U | Unreachable camera does not identify power failure |
| Camera Fan Failure | U | Vendor hardware MIB and actual fan presence required |
| Camera Video Signal Failure | U | Vendor channel/video alarm MIB required |
| Camera Storage Failure | U | Vendor storage-alarm MIB required |
| Camera Edge Storage Full | C-THRESH | Only if supported storage metric and explicit policy exist |
| Camera Recording Disruption | U | Vendor recording-state/expected schedule required |
| Camera Authentication Failure | U | SNMP authentication failure is not camera-account authentication failure |
| PTZ Hardware Failure | U | Vendor PTZ fault MIB required |
| Camera Housing Alarm | U | Vendor event/trap MIB required |
| PoE Power Failure via switch | U | POWER-ETHERNET-MIB port mapping, requested power and reason required |

### NVR / DVR — 13 requirements

| Requested error | Result | Capability or limitation |
|---|---|---|
| NVR SNMP Unreachable | F-COMM | Monitoring communication warning |
| Unexpected NVR Reboot | U | Uptime reset alone insufficient |
| Network Interface Down | E-LINK | Observed links only; critical impact must be established |
| High Bandwidth Utilization | C-THRESH | Supported metric and explicit policy |
| High CPU Usage | C-THRESH | Supported metric and explicit policy |
| High Memory Usage | C-THRESH | Supported metric and explicit policy |
| Critical Disk Space | C-THRESH / A-UCD | Supported storage policy, or Net-SNMP disk flag if actually implemented |
| NVR Disk Failure | U | Vendor drive-fault MIB required |
| Storage Array Degraded | U | Vendor RAID/array state MIB required |
| NVR Temperature Critical | U | Vendor hardware alarm MIB required |
| NVR Recording Failure | U | Vendor channel/recording MIB and expected schedule required |
| Camera Disconnected from NVR | U | Vendor channel inventory and expected-connected camera state required |
| NVR Power Supply Failure | U | Vendor hardware alarm MIB required |

## Verification and operational limits

The native test policies use both SNMPv2c and SNMPv3 configurations across seven category names. They prove common rule/lifecycle behavior, not physical OID support for every manufacturer. The Rust UDP lab returns genuine SNMPv2c noSuchObject, noSuchInstance and non-numeric responses, verifying these do not emit device-availability success/failure. SNMPv3 transport/security code was retained; comprehensive new SNMPv3 hardware certification was not performed.

Lifecycle tests cover repeated polls/replays, one correlated episode, acknowledgement, recovery, reopening, maintenance suppression, disabled rules, equivalent-rule deduplication, sustained thresholds, counter baseline/reset handling, nonpriority devices and unsupported data. A loopback SMTP server actually received the native opening/recovery messages after a temporary failure/retry. Existing Zabbix email retry/restart/multiple-recipient tests were also rerun. MongoDB validation uses the isolated `zaddesh_validation` database, never the live database.

The live database contains 24 devices, 5,580 checks, 13 credential records, zero native AlertRule records and zero SMTP settings/rules/recipients. These empty native policies/settings were preserved. Thus production native CPU/memory/etc. policy and real email delivery are **not configured**, even though their mechanisms pass fixtures. Existing Zabbix alert policy remains authoritative where configured. No arbitrary per-device thresholds or external email recipients were added.

There is no active SNMP trap receiver or trap-to-incident worker in the existing runtime. A database trap model is not a working receiver. Implementing a new trap subsystem would exceed this request's restriction against architecture changes. Trap-only events remain unsupported. Event-specific Zabbix integration can supply already-configured upstream faults; that is not proof of native detection.

Configured native email routing honors existing recipient/category/host/group/port filters. No implicit match is fabricated for Zabbix-only host/port filters on native-only telemetry. Capacity limits and source freshness apply. Replay/episode/per-recipient deduplication is verified; semantic deduplication between independently generated native and upstream events with unrelated identifiers is not established. A counter reset/wrap interval is withheld rather than interpolated. Missing data does not automatically resolve a fault, and long stale incidents are subject to the existing eight-hour current-view policy.

## Manufacturer references

- [Cisco CISCO-ENVMON-MIB](https://github.com/cisco/cisco-mibs/blob/main/v2/CISCO-ENVMON-MIB.my): voltage state `.1.3.6.1.4.1.9.9.13.1.2.1.7`, temperature state `.3.1.6`, fan state `.4.1.3`, supply state `.5.1.3`; normal=1, warning=2, critical=3, shutdown=4, absent=5, monitor-not-functioning=6.
- [Net-SNMP UCD-SNMP-MIB](https://github.com/net-snmp/net-snmp/blob/master/mibs/UCD-SNMP-MIB.txt): configured disk/load/process error flags `.1.3.6.1.4.1.2021.{9,10,2}.1.100.<index>` and configured swap flag `.1.3.6.1.4.1.2021.4.100.0`; clear=0, agent error=1. These are not universal MIBs on arbitrary cameras, firewalls or servers.

## Final results

| Check | Observed result |
|---|---|
| Selected baseline incident/email/topology suites | 24 passed before edits |
| Core regression, excluding separate native-Mongo suites | 134 passed, 1 skipped; 76.01 seconds |
| Selected real MongoDB regressions | 5 passed; 17.56 seconds |
| Collector unit/protocol regressions | 7 passed, including real UDP exception responses |
| Collector Clippy / format / release build | Passed |
| Ruff / core mypy | Passed; existing untyped-body notes/deprecation warnings remain |
| Unchanged frontend TypeScript / Vitest | Passed; 10 UI unit tests |
| Native SMTP opening, retry, recovery and duplicate-rule behavior | Actual loopback delivery passed |
| Native intake beyond first 200 episodes | 201-episode regression passed |
| Production configuration preservation | All captured configuration hashes matched; collection list unchanged |

Evidence logs: `.audit/local-run/critical-release-regression.log`, `critical-mongo-tests.log` and `critical-live-verification.json`. The broad native migration suite was not claimed as passed; this task ran selected native tests and did not repair the previously documented unrelated migration utility. Linux execution and physical certification of other vendors remain unverified.

After restarting only the owned API/collector stack, the installed collector SHA-256 matched the release build. Windows initially held the collector executable open; stopping the owned process and waiting for exit released the lock, after which installation succeeded. The disk spool was preserved.

| Live endpoint | HTTP | Elapsed seconds | Result |
|---|---:|---:|---|
| `/healthz` | 200 | 0.010 | Healthy |
| `/readyz` | 200 | 0.014 | Ready |
| Native current Critical Errors | 200 | 0.837 | Zero current entries |
| Zabbix Critical Errors | 200 | 0.898 | Zero current entries |
| Network diagram list | 200 | 0.154 | Three diagrams |
| Existing UI at port 18875 | 200 | Not timed | Served successfully |

These are current-environment smoke timings, not a 5,000-device load certification. No physical outage was injected into the user's devices. All fault injections were confined to tests/loopback labs. There are no production SMTP recipients/settings to verify real external delivery against. Native queue capacity remains 2,000 pending deliveries; new unseen episodes older than five minutes are not retroactively emailed, and the existing eight-hour current-alert window is retained.

Commits: `3c41c8c` (collector) and `29fddd7` (core alert/notification fixes and regressions). Full requirement matrix: 127 rows. This audit closes the verified implementation defects; it does not certify the U/conditional entries as working device detections.
