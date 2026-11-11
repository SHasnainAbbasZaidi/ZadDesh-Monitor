# ZadDesh add-on features report

The incident workspace and Network Diagram Beta are implemented behind independent flags that default to off. Both were tested against disposable instances. See the [add-on guide](ADDON_USER_GUIDE.md) for migration, configuration and operation.

## What shipped

- **Incidents.** A single workspace for Alerts, Events and Logs. Short titles expand into full detail. Alerts support search, filters, saved views, a desktop expansion and mobile drawer, a timeline, a chart when samples exist, related records, and Admin actions. Buffered probe transitions and confirmed SNMP interface transitions feed stable incidents with automatic recovery. Webhook delivery records are persisted and retried.
- **Network Diagram Beta.** Admins select existing SNMPv3 seed checks with vaulted credentials. The Rust collector polls fixed read-only OID trees. The Python core handles job leases, CIDR checks, LLDP and CDP link interpretation, bidirectional deduplication, status and counter-based utilization, change review, and publishing. The browser handles canvas rendering and editing. Published graphs can be saved as a read-only widget on a User’s Overview. WebSocket invalidation has a polling fallback.

### Scanopy decision

Scanopy is AGPL-3.0. The ZadDesh baseline has no repository license file, so reuse permission under compatible terms could not be established. We used automatic discovery and shareable maps as product references and implemented discovery, merging, rendering, and publication in the ZadDesh codebase. No Scanopy code or assets were copied. [Scanopy license](https://github.com/scanopy/scanopy/blob/main/LICENSE.md)

## Verification

| Evidence | Result |
| --- | --- |
| Windows core, MongoDB configured | `python -m pytest -q core/tests` passed 27 tests. Ruff passed and mypy found no issues in 40 source files. |
| Windows web | `npm run build` passed TypeScript and Vite, `npm run lint` passed, and Vitest passed 1 test. Vite reported 77.58 kB gzip for the entry chunk and 13.66 kB gzip for the graph chunk. |
| Windows browser, flags enabled | `python scripts/audit/browser.py --db .audit/browser-addon-final.db --port 18835 --capture audit-screenshots/addons --grep "add-on workspace"` passed in 13.0 s using Chrome. SQLite migrated from empty to revision `0007_incident_deliveries`. The browser opened an incident over an active WebSocket, displayed the chart and mobile drawer, expanded a real MongoDB log with the fixture credential masked, created a diagram, showed no Admin tab for the User, and saved a published widget that became unavailable after unpublishing. The diagram screenshots use explicitly named lab fixture nodes. |
| Windows browser, flags disabled | The existing browser suite passed five tests at 390, 768 and 1440 px, covering the monitoring flow, SNMP setup downloads and Settings access. It skipped the flag-gated add-on test and the legacy log test, which requires a separate browser MongoDB fixture. |
| Windows Rust | `cargo fmt --check`, `cargo clippy -- -D warnings`, and `cargo test` passed. The collector test suite ran 2 tests. Windows live SNMP topology discovery remains unverified. |
| Ubuntu under WSL, real Rust collector and Net-SNMP | `python scripts/audit/addon_lab.py --collector <debug-collector>` measured a real HTTP outage at **5.04 s** to open and **4.87 s** to resolve the same alert at a 5 s polling interval. The receiver recorded an open request, an open retry, and a resolved request. A SHA-1/AES-128 SNMPv3 seed collected **59 OIDs, 2 nodes and 1 link** using a controlled LLDP subtree. The second seed’s wrong password produced a clear failure and the map still built. A Linux desktop browser and native Windows discovery were unverified. |
| Backend link and alert acceptance | The API test submits controlled LLDP and IF-MIB observations through the leased collector result route. It asserts one high-confidence link for both directions, down status after the LLDP neighbor disappears, and recovery of the same correlated link alert. The same test asserts 403 for a User on Admin routes, 422 for an out-of-range seed, credential absence from the graph, and published/unpublished widget boundaries. The buffered flap test asserts five failures, one alert ID, resolved duration, a flapping badge value, and no increase on replay. A physical port outage and timed live link alert remain unverified. |

### Screenshots

| View | Light | Dark |
| --- | --- | --- |
| Incident desktop | [1440 × 960](audit-screenshots/addons/incident-light-desktop.png) | [1440 × 960](audit-screenshots/addons/incident-dark-desktop.png) |
| Incident mobile | [390 × 844](audit-screenshots/addons/incident-light-mobile.png) | [390 × 844](audit-screenshots/addons/incident-dark-mobile.png) |
| Network Diagram | [Desktop](audit-screenshots/addons/diagram-light.png) | [Desktop](audit-screenshots/addons/diagram-dark.png) |

## Known gaps

| Priority | Area | Remaining work |
| --- | --- | --- |
| High | Discovery | Beta accepts explicit inventory seeds. Subnet expansion, neighbor recursion and depth limits are unimplemented. The collector queries bridge, VLAN, ARP and routing trees, but the core currently infers links from LLDP/CDP. MAC/ARP inference, trunk and port-channel reconciliation, and unmanaged probes remain open. |
| High | Link freshness and acceptance | Node health and alert badges update from the core. Link measurements update on demand. Scheduled rediscovery, physical link-down timing, and live verification of all requested SNMPv3 security combinations remain open. |
| Medium | Diagram controls | Minimap, VLAN grouping and filtering, group collapse, and dedicated widget kiosk verification remain open. New discovered peers appear as part of link observations. Review tracks links and their missing/status observations. A separate new/missing-device lifecycle is absent. A deleted discovered item can reappear when observed again. SVG uses stored positions or the static layout calculation. PNG captures the rendered canvas. |
| Medium | Incident context | Links from raw SNMP traps to incidents and explicit per-interface metric series and neighbor context remain open. Related MongoDB logs use time and source matching. The new timeline tracks actions from the workspace. Legacy alert actions do not add those entries. Named credential fields and common credential formats are masked, with unlabeled secret detection still open. |
| Medium | Performance and regression scope | A measured 500-node FPS result, Linux browser regression, and PostgreSQL migration verification are outstanding. |

## Existing files touched

| Existing paths | Reason |
| --- | --- |
| `core/zaddesh_core/config.py`, `main.py`, `models/__init__.py` | Register the default-off settings, optional delivery loop, and new models. Existing migrations were left intact. |
| `core/zaddesh_core/api/v1/__init__.py`, `logs.py`, `metrics.py`, `stream.py` | Mount add-on routes, gate incident processing and filtered log masking, and add the WebSocket alongside the existing SSE stream. |
| `collector/src/config.rs`, `main.rs`, `probes/snmp.rs` | Wire the optional topology worker and reuse the existing authenticated SNMP session. |
| `web/src/App.tsx`, `components/layout/AppLayout.tsx`, `components/layout/Sidebar.tsx`, `services/api.ts`, `web/vite.config.ts` | Gate the navigation and Overview widget, pass API cancellation signals, and proxy the development WebSocket. Component and service paths in this row are under `web/src`. |
| `web/src/components/ui/MetricChart.tsx`, `MetricPlot.tsx`, `Modal.tsx` | Add optional incident interval annotations and an optional mobile drawer. |
| `CHANGELOG.md` | Record the features under Unreleased. |

## Git history

- `c2b44cc` adds incident and topology services, migrations, API tests and the Linux lab runner.
- `b330454` adds SNMPv3 discovery in the collector.
- `8cd9d46` adds the workspace, diagram, widget and browser verification.
- The documentation commit adds this report, the user guide, the changelog entry and the captured browser screenshots.
