# Operations features delivery — 2026-11-24

## Scope and implementation

The previously committed updates were pushed to `origin/main` first (`028e283..01fc21c`). This delivery extends the existing Rust collector, FastAPI routes, React components and MongoDB-backed Dashboard/Widget/Check/TopologyNode records. No models, migrations, storage engines, credentials, thresholds, notification policies or saved topology positions were replaced. Runtime correlation and chart calculations are deterministic; no AI service or internet lookup is used.

| Request | Implementation and verified behavior | Limits / setup |
|---|---|---|
| Live endpoint tracing | New admin tab, username/MAC/IPv4 search, learned BRIDGE/Q-BRIDGE forwarding entries, bridge-port → ifIndex mapping, ARP correlation, timestamps and stale labels. Real deployment returned 198 observations from two SNMP sources. | A trunk may observe the same endpoint on multiple switches. Entries are labelled candidate access, transit/uplink or ARP adjacency, rather than asserting an unproven final access port. Username requires structured authentication events containing `username` and MAC/IP; SNMP alone generally cannot supply it. |
| Custom dashboards | Existing MongoDB Dashboard and Widget records store time series, latest metric values, existing diagrams, selected interface connectivity, WAN usage donuts, active alerts and recent events. Drag/drop, keyboard move controls, editable titles, compact/full widths, widget removal, dashboard deletion, per-widget range and Overview pinning. | Admin-owned dashboards; existing Viewer access remains unchanged. Feed widgets use native alerts/events. A device or metric must already be monitored. |
| Combined infrastructure view | Automatic topology appears with Infrastructure Device Monitoring on Overview; separate Network Diagram page and previous publishing controls remain. | Existing diagram feature flag applies. Read-only previews preserve saved positions, hide names initially to reduce overlap, and support pan/zoom, hover and selected-device details. |
| Firewall WAN interfaces | Select reported interfaces explicitly. Link status, RX/TX traffic, reported capacity, last download/upload measurements and last attempt timestamp/reason are separate. | SNMP link-up is not proof of internet reachability. A missing measurement remains unavailable. |
| Rotating speed tests | One existing HTTP Check per configured device invokes the Rust source-bound probe. Serial rotation, source IP binding, environment proxy disabled, redirects disabled, normal TLS validation, bounded request time and transfer size. | Disabled until configured. Set each actual collector source IP and a download URL; optional upload URL must acknowledge a completed body. OS routing must force that source through the intended WAN. Tests are serial within each collector; in multi-collector deployments, existing site/task assignment must isolate the intended collector. |
| Device port layout | Read-only detail diagram uses reported physical connectors; green online and gray down/unused/unknown. Selected details follow current polling data. Live API exposed 115 interfaces and 55 explicitly reported physical connectors. | Agents without `ifConnectorPresent` show observed Ethernet interfaces with an explicit physical-count-unavailable label; no guessed 24/48-port chassis. Other port/status views are unchanged. |
| WAN usage donut | Percentage share from receive + transmit counter deltas divided by elapsed seconds for selected WAN links. Excludes stale/missing counters; no filled chart for zero traffic. | Two valid snapshots required. Counter resets, discontinuities and 32/64-bit source changes skip that interval. Percentages describe observed traffic rate share, not purchased bandwidth, cumulative billing usage or an internet speed test. |

FDB IDs are retained as FDB IDs, not incorrectly labelled VLAN IDs. Collection remains bounded to 512 observations per column and the existing 8,000-value / 55-second discovery budget. Restricted SNMP views, VLAN contexts, absent vendor MIBs and large tables can limit coverage. Current ARP correlation is IPv4. Structured username lookup is tested with an authentication event, not certified against an actual domain/RADIUS/firewall integration in this environment.

## Fixes found while executing

- New interface view initially scanned metric history for a nonexistent WAN prefix and returned MongoDB timeouts on the live database. It now resolves configured WAN check IDs first and does not touch raw history when none exist. Added a regression asserting no metric-history query in that case. Live retest: HTTP 200.
- Failed WAN probes cannot mark the firewall offline or manufacture HTTP availability. WAN outcome updates only its check state; normal availability aggregation excludes throughput checks. Verified on isolated SQLite HTTP fixtures and native MongoDB.
- Dashboard pin checkbox now responds immediately while its save completes. Editing waits for the saved-widget request, preventing late loading from overwriting additions. Browser checks verify the saved widget through the API and wait for the actual diagram canvas on Overview.
- Raw interface traffic remains measurable when capacity is unknown, without guessing a utilization percentage. Counter-source changes cannot create an artificial traffic spike.
- IP strings resembling twelve hex digits cannot be mistakenly normalized into MAC matches.

## Verification

Windows host, existing authenticated MongoDB replica set and the installed GNU Rust toolchain were used. Native storage tests clear only the dedicated `zaddesh_validation` database. Existing live devices and credentials were retained; no fake devices or sample metrics were inserted into the live database. Temporary QA dashboards are removed after verification.

| Check | Result / evidence |
|---|---|
| Core regression | **143 passed, 19 skipped** — `PYTHONPATH=core ...python -m pytest core/tests -q`; `.audit/local-run/operations-regression.log`. Skips include environment-dependent/native DB tests; not counted as passes. |
| Native MongoDB | **1 operations integration test passed**: saved dashboard/Widget JSON, private diagram reference, endpoint evidence, interface data, WAN settings and ordinary health aggregation. `.audit/local-run/operations-native.log`. |
| Rust | **8 passed**, Clippy `-D warnings`, rustfmt and release build passed. Includes actual loopback HTTP transfer, verified source peer address, two-interface rotation and no-target failure; no external WAN speed is claimed. |
| Web | TypeScript and ESLint passed; **12 Vitest tests passed**; production build passed. Build retains existing/static shared modules, with chart/map rendering loaded separately; generated shell reported about 108 KB gzipped before final small edits. This is not a Lighthouse claim. |
| Deployed APIs | `/healthz` and `/readyz`: **200**, MongoDB connected. `/dashboard-tools/endpoints`: **200**, 198 observations; `/dashboard-tools/interfaces`: **200**, 24 devices / 115 interfaces / 55 connectors at the sampled time. Counts may change with discovery. |
| Extra browser checks | **Passed**: 34 reported physical connectors rendered for a real switch; live endpoint rows visible. Isolated, browser-only firewall fixture verified 33.3% / 66.7% WAN shares and source-IP/URL settings submission. No fixture device, counters or WAN check was stored in live MongoDB. |
| Chrome | Dashboard create → add existing diagram → save → read back from MongoDB → pin → render on Overview → delete QA dashboard. Desktop 1440, tablet 768 and mobile 390 px: no document overflow or browser page/API 5xx errors in the final flow. Both system theme variants captured. |

Regression commands use the workspace virtual environment and existing dependencies. Repeat the UI workflow with `node scripts/verify_operations_ui.cjs` from the repo root after setting `ZADDESH_LIVE_TEST_TOKEN` to a temporary admin session. This script writes and then deletes a temporary dashboard against local API port 8000 and UI port 18875; use an isolated/test deployment when repeating unattended. It stores screenshots and results under `.audit`, not in Git. No credentials are embedded.

Local screenshot evidence: `.audit/ui-polish/operations-builder.png`, `operations-overview-dark.png`, `operations-overview-light.png`, `operations-768.png`, `operations-390.png`, `operations-real-endpoints.png`, `operations-real-ports.png`. Network inventory screenshots are deliberately excluded from the Git push.

## Operational limitations

- Actual internet throughput through each remote firewall WAN has **not** been measured: per-WAN source IPs, routing and controlled test URLs still need configuration for the deployment. Loopback success proves binding and transfer/rotation logic, not remote path selection. The bounded download measures throughput to that server including connection overhead; server limits and small samples can understate fast links.
- Linux runtime and hardware from every firewall manufacturer were not available for this delivery. No blanket all-vendor or all-device capability claim is made.
- Real SNMP forwarding/physical-port inventory was observed; username identity was tested in a controlled structured-event fixture. Unsupported identity/FDB/ARP/physical connector capabilities remain unavailable.
- Existing host memory pressure and occasional slow large ingestion batches remain operational risks; this change is not a full load/soak test or a new production-readiness certification.

Standards used: [BRIDGE-MIB RFC 4188](https://www.rfc-editor.org/rfc/rfc4188.html), [Q-BRIDGE-MIB RFC 4363](https://www.rfc-editor.org/rfc/rfc4363.html), and [reqwest source-address binding](https://docs.rs/reqwest/latest/reqwest/struct.ClientBuilder.html).


## Files and versions

- `35ed04a`: Rust HTTP WAN runner/real loopback regression and SNMP discovery roots (`collector/src/probes/http.rs`, `snmp.rs`).
- `61cbf2a`: endpoint correlation, existing-model dashboard/WAN API, ingestion health isolation, interface rates and SQLite/native MongoDB regressions (`core/zaddesh_core/api/v1/dashboard_tools.py`, `metrics.py`, `__init__.py`, `endpoint_tracing.py`, `topology.py`, `core/tests/test_dashboard_tools.py`, `test_operations_mongo.py`).
- `7027ad7`: tabs, custom widgets, port diagrams, Overview combination, traffic donut, styling and chart regressions (`web/src/App.tsx`, `components/layout/Sidebar.tsx`, `components/ui/TrafficDonut.tsx`, `pages/Operations.tsx`, `Operations.test.ts`, `operations.css`, `Devices.tsx`, `Overview.tsx`). A following UI fix keeps the read-only port diagram above long log tables.
- Documentation and repeatable browser workflow: this report, `AUDIT_REPORT.md`, `scripts/verify_operations_ui.cjs`.

During an interrupted final verification session, MongoDB and the UI preview were no longer listening. Restored both using their existing configuration, then reran the extra browser checks successfully. The immediate process-exit cause was not established by this check; it is not reported as a proven new out-of-memory incident.
