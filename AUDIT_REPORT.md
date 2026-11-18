# ZadDesh engineering audit

**Date:** 5 November 2026 (Asia/Karachi)  
**Readiness: 55/100 — not production-ready.** The score reflects engineering judgment across the tested feature and security gates. The core and web UI can support a local evaluation, and real collector probes produced useful data. The complete outage-to-notification scenario did not pass.

The largest remaining risks are:

1. **Incident delivery is absent.** Availability incidents open and recover, but there is no persistent notification outbox or working email/chat/webhook worker. The Ruby rules evaluator returns decisions; the core does not call it.
2. **Protocol and inventory coverage is incomplete.** Numeric SNMP polling now works in the tested lab. Generic UDP, traps/informs, discovery, MIB management, topology, and Grafana integration remain absent.
3. **Operational security assumes a trusted monitoring domain.** The core defaults to loopback. Remote use needs TLS, trusted proxy configuration, request limits, and collector egress restrictions. Site/group IDs are filters, not authorization boundaries.
4. **Long-term and failure guarantees are incomplete.** There is no automatic retention or rollup job. Spool replay works in the tested interruption, but hard-power loss, disk exhaustion, and a long collector soak remain unproven.
5. **Legacy installs require care.** The supplied SQLite files, old bundles, and old deployment configurations may contain demo users and published default credentials. The new default uses a fresh DB. Do not expose an upgraded legacy installation until those credentials have been rotated or the data has been migrated offline.

## Environment and Phase 0 baseline

The working directory was a source snapshot; `git status` and `git rev-parse --show-toplevel` returned **not a git repository**. No `.git`, Compose file, CI workflow, or `AGENTS.md` was present initially. The supplied SQLite DB/WAL files were retained. All exploit, benchmark, and UI runs used disposable data.

| Host/tool | Observed |
| --- | --- |
| Windows | NT 10.0.22631, x86-64, 12 logical processors, Intel family 6 model 158. B360N WIFI, 17,109,598,208 bytes of physical RAM (~15.9 GiB), measured after the initial WMI permission failure. |
| Windows runtimes | Python 3.14.7, Node 24.14.0, npm 11.19.1, rustc/cargo 1.99.1, MinGW-w64 GCC 14.2. |
| Linux | Ubuntu 24.04.1 WSL2, kernel 6.6.87.2, 12 logical processors, ~7.9 GiB visible to WSL. Python 3.12.3 and Ruby 3.2.3/Bundler 2.4.20. Ubuntu 22.04 could not attach its virtual disk; Ubuntu 24.04 worked. |
| Docker | Docker CLI 29.4.0; daemon pipe missing, and original `docker compose config` returned “no configuration file provided.” Docker deployment was not verified. |
| Browser | Installed Chrome, exercised with Playwright on Windows at 390, 768, and 1440 CSS pixels. |

Inventory commands: `Get-ChildItem -Force`, depth-limited `Get-ChildItem -Depth 3` per service (repository depth four), `rg --files --hidden` excluding dependencies/build products, and manifest/source reads. The original tree and health table describe the handoff **before fixes**:

```text
.
├── README.md, Makefile
├── config/zaddesh.yaml
├── scripts/dev.ps1, dev.sh
├── collector/
│   ├── Cargo.toml, Cargo.lock, .cargo/config.toml
│   └── src/main.rs, config.rs, scheduler.rs, sender.rs, buffer.rs, types.rs
│       └── probes/{mod,http,icmp,tcp}.rs
├── core/
│   ├── pyproject.toml, alembic.ini
│   ├── alembic/{env.py,script.py.mako,versions/0001_initial_schema.py}
│   ├── tests/{__init__,conftest,test_auth,test_devices,test_metrics}.py
│   └── zaddesh_core/
│       ├── main.py, database.py, config.py, security.py, seed.py, __init__.py
│       ├── api/{__init__,deps}.py; api/v1/{__init__,alerts,auth,checks,collectors,devices,metrics,system,ws}.py
│       ├── models/{__init__,alerts,auth,checks,infrastructure,metrics,snmp,system}.py
│       └── schemas/{__init__,alert,auth,check,device,metric,system}.py
├── automation/Gemfile, lib/zaddesh_automation.rb
└── web/
    ├── package.json, package-lock.json, vite.config.ts, tailwind.config.js
    ├── tsconfig.json, tsconfig.node.json, postcss.config.js, index.html
    └── src/{App.tsx,main.tsx,index.css,components/,context/,pages/,services/,types/}
```

Generated/packaged directories: root `.venv`, `collector/target`, `web/node_modules`, `web/dist`, Python caches, and root/core SQLite DB/WAL/SHM files. `core/.pytest_cache` access was restricted during the inventory. The depth view showed five page files, three layout components, and four UI primitives. There was no requirements.txt; Python dependencies came from pyproject.toml.

| Baseline component | Builds? | Runs? | Tests exist? | Evidence / deviation |
| --- | --- | --- | --- | --- |
| Python core | Yes | Yes | Yes | Clean venv; `alembic upgrade head` reached `0001_initial_schema`. `uvicorn` listening; health/readiness 200, setup 201, login 200. Seven tests passed, 68% statement coverage. Ruff reported 333 findings; mypy 57 errors. |
| React / Vite | Yes | Yes | No | Clean `npm ci` installed 137 packages; Vite 5 build 60.54 kB gzip initial JS. Browser captured five pages. `npm run lint/typecheck` missing; no Vitest package. Handwritten SVG charts, no ECharts. |
| Rust collector | Blocked initially | Unverified initially | No | `cargo clean` worked in a fresh target dir. MSVC `link.exe` missing; installed GNU entry was missing its toolchain manifest. fmt/clippy components missing from original MSVC install. Collector source used OS ping, HTTP and TCP only. |
| Ruby automation | Bundle installed on Linux | Scaffold only | No | Worker printed “Connected” without making a request, then exited. `rspec`: 0 examples; RuboCop: 5 offenses. |
| SQLite | Yes | Yes | Via core | Live API and empty DB migrated; WAL configured. Rollup tables existed without jobs. PostgreSQL/TimescaleDB not run. |
| Combined stack | No | No | No | Windows script changed directories then could not find its relative interpreter; Linux script only launched core and referenced a missing requirements file. No Compose or health orchestration. |

## Phase 1 and regression

| Check | Result / evidence |
| --- | --- |
| Windows Python | Fresh venv; Alembic migration through `0003_query_indexes`. `alembic check`: “No new upgrade operations detected.” Final regression: 17 tests, 78% statement coverage. Ruff passed the repository's explicit error/undefined-name profile; mypy reported no issues in 32 source files. |
| Linux Python | Python 3.12.3, fresh WSL venv. Migrated SQLite; [final Linux checks](audit-evidence/linux-core-final.log): Alembic check, Ruff, mypy, and 17 tests passed, with 78% coverage. |
| Rust Windows | Clean GNU toolchain installation resolved the missing linker/components. Built and ran a native Windows collector. Intermediate release rebuilds hit paging-file error 1455 and a compiler crash under LTO; release profile now disables LTO. A clean target with LTO disabled and the default unwind strategy finished release compilation in 4m 24s. fmt, Clippy with warnings denied and both tests passed. |
| Rust Linux | Native collector built and ran on Ubuntu WSL. fmt, Clippy and two tests were run; the protocol evidence includes live Net-SNMP. No native Linux release-package deployment was verified. |
| Ruby Linux | Bundle installed. Ten RSpec examples exercised the example rule and eight rejected attack forms. RuboCop: three files inspected, no offenses. Worker/notification integration remains absent. |
| UI | Clean `npm ci`; `npm run build/lint/typecheck` and Vitest run. [Playwright](audit-evidence/e2e-final-verified.log): 3 pass, including light/dark overview axe checks, a real loopback HTTP server via a test ingestion adapter, charts, SSE update, and acknowledgement. |
| Integrated process startup | `scripts/dev_stack.py` starts the migrated core, its built UI, and the Rust collector. Windows [one-command smoke test](audit-evidence/stack-verification.log): UI/health/readiness all 200 and collector online. Ruby has no long-running service to start. Docker daemon unavailable. |
| CI | Added Ubuntu/Windows matrix for migration, web, Playwright and Rust checks. There is no GitHub checkout, remote, or Actions run in this snapshot; CI execution is unverified. |
| README quick start | [Clean Windows source copy](audit-evidence/windows-quickstart.log): fresh venv, npm build, root/health/readiness 200 and setup 201. Bash syntax plus Linux core setup verified separately; the clean Linux Node quick start remains unverified. |
| Static analysis | Converted core ORM columns to SQLAlchemy 2 typed mappings without migration drift; mypy passed. The configured Ruff gate checks correctness rules; the original broad 333 findings included style and modernization advice outside that gate. |

## Feature verification

**PASS** means the named behavior was executed with the evidence shown. **PARTIAL** means only a subset was exercised or implemented. **FAIL** includes a required acceptance criterion that is unmet. **NOT IMPLEMENTED** means no executable implementation was found for that behavior. Status is not inferred from a library's capabilities.

### Collection

| Feature | Status | Evidence / fix status |
| --- | --- | --- |
| ICMP reachable, RTT min/avg/max, loss | PASS | Windows collector lab ingested loopback `icmp.rtt_avg` and availability; collector parser test checks Windows and Linux summaries and preserves measured zero. Core consumes the measured status. |
| ICMP unreachable, partial loss, jitter | PARTIAL | Parsed loss and real per-reply RTT variation; jitter omitted when Windows only reports “<1 ms.” Exact unreachable and partial-loss injection are not covered by the loopback proof. |
| ICMP IPv4/IPv6 | PASS | Windows real lab reports both `127.0.0.1` and `::1` healthy. |
| Traceroute | NOT IMPLEMENTED | No probe or API. |
| HTTP methods, headers, auth | PARTIAL | Real HEAD 204 and GET exercised; POST/PUT paths exist but were not exercised. Custom headers/auth configuration is absent. |
| HTTP redirects, slow endpoint, 4xx/5xx, body assertions | PASS | Windows lab: 302→307→200; configured 404 accepted; HEAD 204; deadline exceeded; body match/mismatch; 503 opens incident. |
| HTTP total vs DNS/connect/TLS/TTFB/transfer | PARTIAL | Response time and size collected. Individual timing phases and waterfall unavailable. |
| HTTP status/body, JSONPath/regex, transactions | PARTIAL | Exact status and substring tested. JSONPath/regex and multi-step transactions absent. |
| TLS expiry/issuer/chain/hostname | PARTIAL | Removed blanket certificate bypass. Real untrusted and expired-untrusted targets rejected. Expiry cannot be isolated from untrusted issuer in that fixture; issuer/expiry/hostname telemetry and independent chain tests absent. |
| TCP connect time/banner | PARTIAL | Real listener returned a banner and `tcp.available=1`; collector has connect time and reads a bounded banner. Banner is not persisted as a metric or assertion. |
| TCP send/expect and DNS/SMTP/SSH/MySQL/Redis/NTP presets | NOT IMPLEMENTED | Only simple connect/banner probe in this audited implementation. |
| Generic UDP | NOT IMPLEMENTED | No UDP check path in supplied collector. |
| SNMP v1/v2c GET/GETNEXT/GETBULK/WALK | PASS | Unprivileged local Net-SNMP 5.9.4: v1 GET, v2c GETNEXT, GETBULK and WALK ingested real uptime/interface counter values. GETBULK is not a v1 operation. |
| SNMP v3 auth and privacy combinations | PARTIAL | Direct Net-SNMP preflight succeeded, and the Rust collector ingested v3 SHA-1 authNoPriv and SHA-1/AES-128 authPriv samples. Other combinations/noAuthNoPriv were not run. [snmp2 API](https://docs.rs/snmp2/latest/snmp2/). |
| SNMP counter wrap/rates; interface, CPU, memory, disk templates | NOT IMPLEMENTED | No wrap-aware transforms or automatic device profiling. |
| SNMP MIB import/name resolution and LLD | NOT IMPLEMENTED | Schema/UI placeholders were not working tools. |
| SNMP traps/informs v1/v2c/v3, UDP 162, event mapping | NOT IMPLEMENTED | No running trap listener or event pipeline. |
| Scheduler jitter, intervals, retries, deadlines, concurrency and backpressure | PARTIAL | Changed task refresh to replace edited checks, bounded ping subprocesses, and implemented retries. Real lab used 5-second schedules with runtime target changes. Large-scheduler lag, jitter distribution and retry counts were not measured. |
| Core outage buffering, FIFO, replay, dedup | PARTIAL | The Windows collector spooled during core kill and drained after restart. The first Linux replay check timed out waiting for a post-restart sample. A repeat with preserved logs passed and returned one matching sample. FIFO and crash-before-ack retention unit test; server duplicate response tested as 0 inserted. Kill/power-loss guarantee, full disk, high-volume backlog and concurrent spool ownership remain unverified. |

### Core and automation

| Feature | Status | Evidence / fix status |
| --- | --- | --- |
| First-run setup, login, Argon2id and JWT expiry | PASS | HTTP health/readiness 200, setup 201, repeat setup 400, login 200; browser setup/sign-out/login flow; Argon2 password code exercised, JWT forgery/missing-claim regression 401. |
| TOTP 2FA, account lockout, idle timeout | PARTIAL | No TOTP or durable account lockout, no idle timer. Fixed-duration JWT expiry and a process-local per-IP login limiter; 13th attempt returned 429. |
| RBAC Admin/Operator/Viewer, custom and site/group scopes | PARTIAL | Admin-only probe changes, operator incident triage, viewer reads; two low-privilege roles denied probe writes. No custom role enforcement or per-site user isolation. |
| API tokens and audit log | PARTIAL | Token creation/auth/revocation tests and user-token ingestion rejection. Audit entry exists for first setup and acknowledgement records; no full audit UI/export or complete mutation coverage. |
| Ingestion batch size, token and bad payloads | PASS | Authenticated valid batches 202; invalid payload 422; max-size control; wrong collector IDs and user tokens rejected; secret values no longer echoed in validation responses. |
| Ingestion compression and mTLS | NOT IMPLEMENTED | JSON over authenticated HTTP(S), no gzip protocol or native mTLS. |
| Ingestion idempotency | PASS | Alembic fingerprint migration and SQL uniqueness; replay returned 0 inserted and 2 duplicate points; exact samples persisted once in regression. Fingerprints apply to new writes. |
| Raw query and SQLite WAL | PASS | Known point 4.25 returned; latest and raw time-window code exercised, offset inputs normalized to UTC. Metrics DB observed in WAL mode. |
| Rollups 1m/5m/1h/1d, retention, rollup correctness | NOT IMPLEMENTED | Tables and config only; no jobs. UI now states that raw samples accumulate. |
| OpenAPI and entity CRUD/pagination/filtering | PARTIAL | OpenAPI 200; device/check CRUD and selective CRUD/filter routes exist; no full schema accuracy audit or CRUD for all modeled entities. |
| Rate limiting | PARTIAL | Login path tested for 429; ingestion limiter exists but no sustained bypass/distributed test; upstream limiter needed for deployment. |
| Bulk import/export and Grafana datasource | NOT IMPLEMENTED | No routes for these features. Chart CSV export does not replace entity bulk export. |
| Prometheus /metrics | PASS | HTTP 200; text exposition of core uptime, device totals and active alert counts. |
| Threshold/rate-of-change/no-data/hysteresis/flap/maintenance | NOT IMPLEMENTED | Core does not evaluate configured rules or time windows. Ruby DSL can calculate one constrained average rule independently; no live integration. |
| State change, consecutive failures, acknowledge/comment/resolve | PARTIAL | Real HTTP failure creates incident and recovery resolves it. Tests cover dedup and out-of-order status handling; acknowledgement HTTP 200 in Playwright. Counter tracks failures; no configurable consecutive-failure gate. |
| Parent/child suppression, escalation timing | PARTIAL | Ruby evaluates parent-down suppression and returns 900-second escalation decision in RSpec; no production scheduler or dispatch. |
| Ruby DSL example and sandbox | PARTIAL | Example evaluated to average 89.0 and expected team/channel/escalation directives; allow-listed syntax rejects file, network, system, backticks, eval, require, ObjectSpace, and Kernel payloads (10 examples total). No Ruby eval or live core integration. |
| Email/MailHog, webhook, Slack/Teams/Telegram/Discord, templating and retry | NOT IMPLEMENTED | No dispatcher or delivery outbox. No external messages sent. |

### Acceptance

| Requirement | Verdict | Observed |
| --- | --- | --- |
| Fresh setup + add device + live ICMP/TCP/HTTP/SNMP graphs <2 min | FAIL for full requirement | Browser setup-to-HTTP-chart passed at three sizes. Rust lab independently ingested real HTTP, TCP and ICMP metrics within the first few seconds after enrollment. The native Linux lab also ingested v1/v2c/v3 samples by 8.49 s from lab start on the final repeat. The combined all-protocol UI scenario was not verified. |
| Outage + status/incident/notification within two intervals | FAIL for full requirement | Real HTTP 503 opened an incident in 3.93 s at a 5-second polling interval in the Windows lab. Acknowledgement was verified in Playwright. No notification delivery exists. |
| Recovery and incident resolution | PASS for tested HTTP scenario | Real HTTP target changed back to 200 and the incident resolved automatically in 5.18 s in that run. Playwright saw a resolved previously acknowledged incident. |

## Security findings

| ID | Severity | Status | Evidence and remediation |
| --- | --- | --- | --- |
| S01 | Critical | Fixed for fresh config | Forged admin JWT returned 200 under the published default key. Removed key defaults from config, generated a per-process key when unset, enforced required JWT claims, tightened lifetime, and regressed forged tokens to 401. Rotate prior deployments. |
| S02 | Critical | Fixed | Encoded SPA traversal returned YAML (HTTP 200). SPA serves only the fixed index and rejects traversal/API fallbacks; assets are served by the static mount. Regression 404. Added CSP on the SPA and response security headers. |
| S03 | Critical | Fixed for fresh config | Anonymous registration 201, self-issued tasks 200, viewer ingest 202. Enrollment now requires its own secret; collector identities are checked and do not accept user JWTs/API tokens. Registration regression 401, cross-collector access 403, replacement key 409. |
| S04 | High | Fixed for fresh config | Documented demo credentials and a default collector token were available in the UI/config. Removed both; demo now requires explicit settings and a separate DB, and no credentials are displayed. Old shipped DBs/configs require rotation. |
| S05 | High | Mitigated; deployment work remains | Low-privilege probe writers could reach internal services. Only admins can configure probe targets now; public setup requires loopback. Restrict collector egress, including redirects and DNS changes; no multi-tenant per-site isolation. |
| S06 | High | Fixed in probe, partial transport assurance | Collector accepted all TLS certs. Untrusted TLS rejected in real lab after removal. Core stays loopback by default; remote TLS proxy and mTLS are not provided. |
| S07 | High | Mitigated | Known default encryption key removed; AES-GCM requires an explicit key, random nonce, and fails visibly on invalid ciphertext. SNMP checks reference collector-local credential aliases; API rejects literal passwords/communities. No key rotation or production secret store integration verified. |
| S08 | High | Fixed in UI dependencies | Baseline npm audit: 1 high and 1 moderate (Vite/esbuild). Updated Vite/React plugin, final runtime-only audit and full dev advisory results below. |
| S09 | Medium | Partial | Bounded process-local rate limits and non-finite/bad metric payload validation. No distributed lockout or rate limits across workers, and no sustained ingestion denial-of-service proof. |
| S10 | Medium | Open | Session bearer in browser local storage; protected by SPA CSP and React escaping but still exposed to script if XSS exists. No cookies-based CSRF surface in these flows; no full XSS/CSRF scanner run. |
| S11 | Medium | Open | Untrusted strings observed through React text rendering; no broad dynamic SQLi, stored XSS, malformed trap, or IDOR scan. Existing SQLAlchemy queries bind parameters; site/group filters are not security scopes. |
| S12 | Medium | Partial | Audited services used unprivileged Windows/WSL processes with loopback and user-space ping. Linux service packaging, Windows Services, remote process privilege, and trap port capabilities not verified. |

Dependency and secret scans:

| Check | Evidence |
| --- | --- |
| npm runtime | [npm audit --omit=dev](audit-evidence/web-runtime-audit.json): zero reported vulnerabilities. |
| npm full | [npm audit](audit-evidence/web-full-audit.json): seven moderate dev-chain entries, zero high/critical. Baseline was [one high and one moderate](audit-evidence/baseline-npm-audit.json). |
| Python | [pip-audit](audit-evidence/python-dependency-audit.json): no known vulnerabilities found in the audited venv; the local editable package is not a PyPI dependency. |
| Ruby | The previous advisory checkout had a malformed file. [Fresh advisory database retry](audit-evidence/ruby-audit-clean.log) against the final lockfile: no vulnerabilities found. |
| Rust | `cargo audit` remained unavailable after two tool builds; the Windows scanner build failed and the Linux scanner install stopped before producing a binary. A separate [OSV scan of 219 locked registry packages](audit-evidence/collector-osv-audit.json) reported no matches. |
| Source secrets | `gitleaks detect --no-git --redact` on staged source: no leaks found; manual review separately identified the original hardcoded defaults. Generated artifacts and packaged DBs were excluded. |

SBOMs: [collector](audit-evidence/collector.sbom.json), [Python audit environment](audit-evidence/python-audit-environment.sbom.json), [Ruby gems](audit-evidence/ruby-automation.sbom.json), [web runtime](audit-evidence/web-runtime.sbom.json). No git history could be scanned. Old credentials and lab secrets are not included in this report.

## Performance and reliability

Synthetic generator: `scripts/audit/load_core.py --db NEW_DB --rounds 2 --interval 60`. It inserted 5,000 devices and 50,000 checks into a disposable DB and then submitted one observation per check over HTTP, in 1,000-point batches. Query p95 is for 20 small per-check time-range requests. It does not measure a 50,000-check Rust schedule, CPU/RSS at that scale, or a browser dashboard query.

| Iteration | Check samples | Elapsed | Rows/sec | Batch p95 | Query p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Pre-tune, initial transitions | 50,000 | 61.48 s | 813.3 | 2,224.06 ms | 10.04 ms |
| Pre-tune, steady state | 50,000 | 10.50 s | 4,763.9 | 395.11 ms | 6.61 ms |
| Tuned, initial transitions | 50,000 | 25.77 s | 1,939.9 | 809.23 ms | 8.25 ms |
| Tuned, second round (concurrent builds on host) | 50,000 | 61.12 s | 818.1 | 2,549.12 ms | 52.29 ms |
| Required floor at one sample/check | 50,000 | 60 s | 833.3 | — | — |

The 31-round/60-second soak attempt committed the first 50,000 rows and then stalled in Windows process-tree instrumentation before printing a round result. It was stopped and is **not a soak pass**. At an intermediate spot check the core process showed ~115 MiB working set, 209 handles and 28.4 cumulative CPU seconds; those are one sample, not a leak trend. The harness now makes resource instrumentation opt-in and avoids the recursive process lookup.

The initial transition path improved after batching incident lookup, using bulk writes, and adding missing query indexes. The noisy second round missed cadence. This is not a sustained 50,000-check capacity certification. Raw samples usually contain several points per probe; one point per check underestimates the true ingestion demand. See [throughput chart](audit-evidence/throughput.svg).

Windows passed the core termination and spool replay check at lab size. The first Linux replay attempt timed out waiting for the expected HTTP sample. The repeat created one batch during the outage and drained it after restart, with one matching HTTP sample. The full repeat took 24.52 seconds including setup and protocol tests. [Malformed YAML](audit-evidence/corrupt-config.log) exited nonzero with a parser error; the core did not silently start with defaults. Collector-down visibility uses heartbeat age separately from check status. A collector failure does not fabricate target-down observations, but prolonged missing data and agent recovery are not covered by a no-data incident policy. Disk-full, arbitrary repeated kill tests, and descriptor/memory stability were not validated as release gates.

## Dashboard before and after

The redesigned overview shows status with labels and icons, groups devices by site, and distinguishes loading, stale, empty and live data. Removed hardcoded health, credentials, false availability percentages, and simulated retention. Added responsive navigation, a system/light/dark preference, focus-visible states and native dialogs, device search shortcut, time ranges, lazy ECharts time-series, CSV/PNG controls, and authenticated SSE updates with polling fallback. Screens use disposable local data. The baseline Settings screenshots mask the credential area; auth/profile and system responses used for those two masked recaptures were replayed for the screenshot only.

| Page | Before dark / light | After dark / light |
| --- | --- | --- |
| Overview | [Dark](audit-screenshots/before-dark-overview.png) · [Light](audit-screenshots/before-light-overview.png) | [Dark](audit-screenshots/after-dark-overview.png) · [Light](audit-screenshots/after-light-overview.png) |
| Devices | [Dark](audit-screenshots/before-dark-devices.png) · [Light](audit-screenshots/before-light-devices.png) | [Dark](audit-screenshots/after-dark-devices.png) · [Light](audit-screenshots/after-light-devices.png) |
| Checks | [Dark](audit-screenshots/before-dark-checks.png) · [Light](audit-screenshots/before-light-checks.png) | [Dark](audit-screenshots/after-dark-checks.png) · [Light](audit-screenshots/after-light-checks.png) |
| Alerts | [Dark](audit-screenshots/before-dark-alerts.png) · [Light](audit-screenshots/before-light-alerts.png) | [Dark](audit-screenshots/after-dark-alerts.png) · [Light](audit-screenshots/after-light-alerts.png) |
| Settings | [Dark](audit-screenshots/before-dark-settings.png) · [Light](audit-screenshots/before-light-settings.png) | [Dark](audit-screenshots/after-dark-settings.png) · [Light](audit-screenshots/after-light-settings.png) |

Initial JS was 61.72 kB gzip against a 300 kB target. The lazy ECharts chunk was 180.65 kB gzip. Axe passed on the overview at three viewport sizes in light and dark; other views did not receive a full accessibility audit. No Lighthouse ≥90, frame rate, 30-minute live browser-memory, export UX, or ultrawide NOC certification. SLA/trend/top-N, virtualized/saved/bulk device tables, full connection-test wizard, per-interface panels, check trace and HTTP waterfall, topology, dashboards, kiosk, reports and SNMP tools are still incomplete or absent.

## Bug log and changed-file summary

The original snapshot arrived without Git history. The local repository now tracks the audited source as its first baseline. The groups below describe work that preceded the initial commit.

| ID | Severity | Component / root cause | Fix and regression |
| --- | --- | --- | --- |
| B01 | Critical | Static asset fallback concatenated untrusted paths | Fixed index-only route; traversal security test. |
| B02 | Critical | Published JWT/encryption/collector defaults | Removed defaults, added claim/key checks and local-only setup; forgery, setup and settings tests. |
| B03 | Critical | Collector registration and user-token acceptance | Separate enrollment, collector identity/site checks, authenticated heartbeat, invalid auth tests. |
| B04 | High | HTTP ignored cert errors; HTTP 200 overrode failed assertions | Trust validation on; availability metric follows the probe result; real lab plus regression test. |
| B05 | High | Devices only transitioned unknown→up; no recovery incident state | Bounded check transitions, availability incidents, stale samples ignored; API and Playwright regression. |
| B06 | High | Disk queue deleted entries before acknowledgement; API accepted duplicate points | Spool write/rename/ack, FIFO and restart test, fingerprint migration, real core-restart lab. |
| B07 | High | Quick start failed, migration index drift, original DB could be reused accidentally | Cross-platform launchers, migration-head checks and fresh DB default. |
| B08 | Medium | Windows ping RTT fabricated 1 ms; subprocess target could be parsed as an option; schedule ignored edits | Target validation and deadlines, no fabricated RTT, jitter from real replies, refreshed runners. |
| B09 | Medium | UI showed hardcoded healthy/uptime/retention values | API-derived states, fixed labels and theme/contrast regressions; no fake live values. |
| B10 | High | Ruby scaffold claimed a successful connection without connecting | Removed misleading scaffold behavior. Added a bounded declarative evaluator with no Ruby execution and hostile syntax tests. |
| B11 | High | SNMP worker stack overflowed with inline protocol buffers; unrestricted request IDs interfered with v3 interoperability | Heap-backed buffers and bounded positive request IDs; real Net-SNMP v1/v2c/v3 regression. |

Browser times (390 / 768 / 1440 px): login/setup through first chart **3.029 / 1.871 / 1.934 s**; test HTTP outage through visible incident **0.996 / 1.089 / 1.028 s**; recovery through API resolution **0.081 / 0.076 / 0.068 s**. These browser timings use the lab ingestion adapter, not collector poll intervals. See [verified browser run](audit-evidence/e2e-final-verified.log).

The [Windows collector results](audit-evidence/windows-collector-probes.jsonl) , [Windows release probes](audit-evidence/windows-release-probes.jsonl), and [Linux protocol results](audit-evidence/linux-collector-probes.jsonl) contain the real target observations. Before/after exploit status codes are in [baseline HTTP evidence](audit-evidence/probe-baseline.log). The protocol, browser, and performance logs are in [audit-evidence](audit-evidence/). The detailed coverage statements are limited to the executed fixtures.

Files grouped by change: `core/zaddesh_core/{main,config,security,seed}.py`, API dependencies and v1 auth/checks/devices/collectors/metrics/alerts/system/stream/routes, check/device/metric/system schemas, check/metric models, `core/alembic/versions/0002_*.py` and `0003_*.py`, `core/pyproject.toml`, and core test fixtures and regressions. Removed the unmounted, broken query-token WebSocket module in favor of the bearer-authenticated SSE endpoint. Collector changes cover Cargo manifests/configuration and the configuration, main, scheduler, sender, buffer, types and probes modules. Ruby changes cover the Gemfile/lock, RuboCop config, evaluator and specs. Web changes cover package manifests, Vite/ESLint/Playwright config, layouts, themes, overview/devices/alerts/login/settings pages, chart/modal primitives, SSE client and browser/unit tests. Tooling: README, launchers, stack runner, audit harnesses, ignore rules, Actions workflow and evidence assets.

## Roadmap and outstanding gates

1. Build a durable incident outbox and Ruby dispatcher; exercise local SMTP/webhook endpoints and every provider mock, including retry, dedup and escalation.
2. Complete protocol coverage, then rerun the full acceptance scenario with the real collector. Prioritize SNMP traps/informs, wrap-aware counter rates, interface discovery, and generic UDP.
3. Add data retention, rollups and no-data/heartbeat incidents; test loss/replay across disk-full, process kill and interrupted commits. Enforce one process per spool directory.
4. Add scoped RBAC, TOTP, persistent throttling, upgrade/credential rotation instructions, and hardened remote transport before exposing the stack.
5. Finish dashboard/reporting/topology work and run whole-app axe, Lighthouse and browser-memory checks. Run real CI on both platforms in a Git checkout.

## SNMP device setup follow-up

**Devices → Add Device** now expands an SNMP interface in the same dialog. The target can use the device IP or DNS name. The form supports direct community entry for v1 and v2c, security-level-specific fields and optional context for v3, a custom UDP port, and a repetition limit for GETBULK. Collector aliases still work. Direct entry is available during initial device provisioning.

| Check | Result | Evidence |
| --- | --- | --- |
| Provision SNMPv1, v2c and v3 through the browser | PASS | Playwright passed at 390, 768 and 1440 px. The v3 test saved a DNS target, context and GETBULK repetition limit. Check responses omitted the credentials. |
| Credential storage and delivery | PASS | Core pytest passed 19 tests. The new regression confirmed AES-GCM storage, site and collector identity checks, blocked shared-token and insecure delivery, cache prevention, and deletion when the check is removed. |
| Live polling with stored credentials | PASS | Linux Rust collector returned numeric readings from Net-SNMP for v1 GET, v2c GETNEXT, GETBULK and WALK, and v3 at all three security levels. The noAuthNoPriv case resolved the agent through DNS. [Protocol output](audit-evidence/linux-snmp-interface.jsonl). |
| Named SNMPv3 context against a real target | PARTIAL | The browser and API verify the context reaches the collector task. The live agent exercised the default context. |
| Windows local stack | PASS | Rebuilt the collector, restarted the existing stack on port 8000, verified HTTP 200 for the dashboard and health endpoints, and observed the collector enroll and fetch tasks successfully. Live SNMP wire tests ran under Linux. |

Updated the device form, SNMP request schema, encrypted credential storage and collector handoff, Rust SNMP probe, browser and protocol labs, and README. No Git metadata was present in the workspace.

## Command-line SNMP client setup

**Devices → Add Device → SNMP → SNMP setup** offers Windows and Debian/Ubuntu installers. Downloaded scripts prompt for credentials locally. The dashboard provisions a matching check when the operator submits the device.

| Check | Result | Evidence |
| --- | --- | --- |
| Windows setup download and matching device provisioning | PASS | Playwright downloaded the PowerShell script and verified the saved check uses SNMPv2c on UDP 161. |
| Windows native feature installation and configuration | PARTIAL | PowerShell syntax passed and the host exposed the native SNMP service. The installer was not applied to the existing Windows service. |
| Linux setup download and matching device provisioning | PASS | Playwright downloaded the shell script and verified the saved check uses SNMPv3 authPriv with SHA-1 and AES-128 on UDP 161. |
| Linux service setup and live polling | PASS | The installer started an Ubuntu systemd service with Net-SNMP. `snmpget` read sysUpTime before and after a service restart. The persistent state no longer contained the bootstrap passphrases. Package installation on a fresh Linux host was not exercised. |

## Default SNMPv3 and centralized logs follow-up

The new Default SNMP flow replaces the earlier Windows SNMPv2c installer. Default provisioning asks for IPv4 and UDP port, generates per-device authentication and privacy passphrases, and supplies a matching script for either platform. Custom SNMP retains manual v1, v2c and v3 configuration.

| Check | Result | Evidence |
| --- | --- | --- |
| Default and Custom SNMP | PASS | Playwright verified that Default downloads contain matching per-device SNMPv3 profiles, custom UDP 1161 reaches the saved check, and Custom still provisions v1, v2c and v3. |
| Linux Default agent | PASS | Ubuntu installer started Net-SNMP on UDP 1161. An authenticated and encrypted sysUpTime GET succeeded before and after service restart. |
| Windows Default agent | PARTIAL | Windows PowerShell 5.1 parsed the generated script template. The script configures a separately installed Net-SNMP agent. Compatibility depends on the supplied executable and libraries. Live installation on Windows 7, 8, 10, 11 and Windows Server 2012–2025 remains unverified. |
| Log ingest and access control | PASS | Integration tests used MongoDB Community 8.0.38, verified duplicate UUID handling, source and severity filters, a TTL index, a separate write token and admin-only reads. |
| Log dashboard and syslog forwarding | PASS | Playwright read and filtered records in a disposable MongoDB database. A loopback UDP syslog audit message traversed the forwarder and HTTP ingest and was read back from MongoDB with the expected sender and severity. |
| Deployment scope | LOCAL | MongoDB runs with authentication on loopback and a dedicated application account. The initial UDP syslog receiver also listens on loopback. The README gives the receiver command for a trusted LAN subnet. |

## All Devices latency and MongoDB migration staging

The Probe latency panel defaults to **All Devices** and can switch to one device from the same dropdown. The fleet endpoint queries the first enabled check per device and protocol, keeps the peak successful sample and a failed probe marker per time bucket, and increases the bucket width for larger fleets.

The running `/api/v1/system/info` reported `database_type: sqlite`. MongoDB continues to serve infrastructure logs. The staging command copied all 32 SQLite tables into `zaddesh_migration_primary_snapshot`, including 41,106 raw metrics, and verified each collection's row count and content hash. The snapshot preserves numeric IDs, encrypted credentials, and timestamp microseconds.

Production cutover remains pending. Authentication, inventory, collectors, metric ingestion and incident handling still use SQLAlchemy, including atomic writes across related tables. A MongoDB runtime needs equivalent operations, indexes, and transaction handling before serving this staged data. The staged copy is point-in-time and will need refreshing at cutover.

## 2026-11-18 rollout: SNMP workstations and native MongoDB

**Current status supersedes the historical migration-staging section above.** The running core now uses MongoDB for accounts, inventory, configuration, metrics, incidents, topology and logs. MongoDB mode creates no SQL engine/cache; SQLAlchemy metadata and expression builders remain. Legacy SQL is retained for migration, isolated tests and rollback.

### Delivered

- Discovered Devices lists registered devices with enabled SNMP checks (13 in the live installation); it is not a subnet scanner. New entries here default to SNMP-only, on-demand collection and non-priority alerts.
- Device details request processes, services, CPU and storage through SNMP. Windows/Linux command downloads and existing-client updates include service and issue-log extensions. Missing tables, denied access and collector timeouts have explicit states.
- Priority controls scope Overview, global events, incidents and notifications. Existing infrastructure retains priority. On-demand devices alert on snapshots; choose scheduled collection for continuous checks.
- Infrastructure Logs separates ZadDesh logs and integrated application logs, including inside device details. Zabbix events archive automatically regardless of priority. Other providers can forward tagged records through authenticated ingestion; automatic pull adapters for every integration are not implemented.
- Native log TTL defaults to 30 days. The client extension retrieves the latest 30 warnings/errors on demand, preserves timestamps and deduplicates repeats. Older client events can rotate away before retrieval.

### Migration and live evidence

Authenticated MongoDB 8.0.38 runs as a single-member replica set on loopback port 27118, in private persistent Windows application storage. Application credentials are scoped to core/log namespaces. Protected settings and backups are excluded from Git.

All 54 collections passed document read-back and row-count verification. The initial snapshot contained 13,792,959 metrics; final catch-up preserved **13,856,230 metrics**, 24 devices, 5,570 checks and the admin account. Password hashes and saved node positions were compared successfully. Original SQL files remained unchanged. Initial optimized migration took 1,426.23 seconds; final cutover took 468.38 seconds, including a 208.5-second backup. Polling was interrupted during cutover; this is not a claim of uninterrupted sampling.

Live `/readyz` and `/api/v1/system/info` return 200; system info reports `database_type: mongodb`. Real Zabbix inventory returns 25 hosts. Both log sections contain records and the Zabbix log collector reports ready. Authenticated WebSockets connected through ports 8000 and 18875. The published browser rendered all 13 SNMP devices without API 5xx responses during that walkthrough.

### Bugs fixed

| ID | Severity | Failure and fix | Evidence |
|---|---|---|---|
| MONGO-01 | High | Fingerprint equality selected a collection scan despite the partial unique index, causing full-history ingestion timeouts. Added an explicit string-type predicate. | Real-driver command capture and explain test selects `idx_fingerprint`; duplicate replay is idempotent. |
| MONGO-02 | High | Native storage needed model refresh, write locks and version checks for account/state concurrency. Implemented transactional guards and refreshed hydration. | Concurrent lock, refresh and parallel HTTP ingestion tests. |
| MONGO-03 | Medium | Per-record guards/updates caused excessive round trips. Batched them by collection and deduplicated device-status query results. | 25 locked/updated records require two update commands; real collector queue drained from over 200 batches to one. |
| MONGO-04 | Medium | Concurrent transaction conflicts appeared as database outages. Return retryable HTTP 409. | Parallel ingestion returns 202/409 and stores one record. |
| CHART-01 | Medium | MongoDB ranking rejected multiple window sort fields. Added a composite numeric sort key. | Real MongoDB latency/window/failure-exclusion test and live latency HTTP 200. |
| UI-01 | Medium | Priority checkbox reverted before async save completed. Added optimistic state and failure rollback. | Browser click, saved response and persisted priority assertions. |
| UI-02 | Medium | Issue logs lagged snapshot completion. Refresh logs immediately on completion; display observation times as UTC converted to local time. | Browser sees the issue log within five seconds. |
| TEST-01 | Medium | Background jobs accessed shared SQLite fixtures and caused a Windows access violation. Isolated unit-test workers; dedicated lifecycle tests still enable real loops. | Full legacy regression suite passes. |

### Tests and remaining limits

Core legacy tests: **86 passed**; native-only tests run separately. Real MongoDB suite: **14 passed**, covering migration/catch-up, indexing, transactions, batching, priority, inventory, issue-log deduplication and unavailable collectors. TypeScript, ESLint, production build and **10 Vitest tests** passed. Windows GNU Rust checks passed: clippy with warnings denied, four tests and release build.

An isolated MongoDB browser run passed **7 Playwright tests**, including chart/alert/acknowledgement/recovery at 390/768/1440 pixels, downloads, log sections, user permissions and workstation details. No serious/critical axe violations occurred in the tested workstation flow. Its process/service data is explicitly named validation-fixture data in a separate database, not production seed data.

All four generated installers/update scripts passed PowerShell/Bash syntax checks. The real Windows event reader returned 43 encoded chunks with read access and no stderr using the installer's process-scoped execution-policy argument. The first test omitted that argument and was blocked by the machine's script policy; no permanent policy change was made.

**Real remote SNMP workstation collection remains unverified:** the selected existing SNMPv3 target timed out. The failure was shown explicitly; it was not marked passed. Client agent installation and SNMP access remain prerequisites. Both installed Linux lab distributions failed to start through WSL. Linux deployment and the Windows 7 through Server 2025 compatibility matrix remain unverified.

Observed full-history API times included critical incidents 9.76 seconds and latency 2.06 seconds. These are individual observations, not p95 load results. Ingestion initially failed; the fixed index and batched updates restored accepted batches and disk-queue replay. No new 5,000-device qualification or 30-minute browser soak is claimed. Linux/Windows CI remains configured and a native MongoDB job was added; remote CI was not run.

The rollout is usable but **not production-qualified**. Remaining priorities: real client rollout, compatibility testing, faster incident queries, broader load/soak coverage, and the earlier unimplemented features documented in this report.

### Evidence and changed files

Local live screenshots: `.audit/ui-polish/live-overview-mongo.png`, `live-discovered-mongo.png`, `live-snmp-snapshot.png`, `live-integrated-logs.png`. Fixture screenshots: `workstation-light.png` and `workstation-dark.png` in the same directory. Sanitized test/API reports are in `.audit/local-run/`; protected migration reports/settings are in `.audit/mongodb-native/`. The local restart helper is `.audit/local-run/start-current-stack.py`.

Changes cover core native persistence/query compilation, models/migrations, priority filtering, workstation APIs, integrated logging, collector SNMP jobs, client scripts, Devices/Logs/Overview UI, regression tests, launchers, migration tooling, CI and README. Git commits below record the complete changed-file lists.

Versioned implementation commits:

- `606b1cd docs: record MongoDB migration and workstation validation limits`
- `d45fd36 feat(ui): add SNMP discovery view, priority controls and separated logs`
- `9ea0603 feat(collector): collect on-demand SNMP process and service snapshots`
- `c85726e feat(core): add native MongoDB and priority SNMP workstation inventory`

Changed files for this rollout:

- `.github/workflows/ci.yml`
- `AUDIT_REPORT.md`
- `README.md`
- `collector/src/main.rs`
- `collector/src/probes/snmp.rs`
- `collector/src/topology.rs`
- `config/zaddesh.yaml`
- `core/alembic/versions/0016_device_priority.py`
- `core/alembic/versions/0017_workstation_inventory.py`
- `core/alembic/versions/0018_client_issue_logs.py`
- `core/tests/conftest.py`
- `core/tests/test_email_alerts.py`
- `core/tests/test_incident_workspace.py`
- `core/tests/test_metrics.py`
- `core/tests/test_mongo_store.py`
- `core/tests/test_network_diagram.py`
- `core/tests/test_zabbix.py`
- `core/zaddesh_core/api/v1/__init__.py`
- `core/zaddesh_core/api/v1/alerts.py`
- `core/zaddesh_core/api/v1/collectors.py`
- `core/zaddesh_core/api/v1/devices.py`
- `core/zaddesh_core/api/v1/email_alerts.py`
- `core/zaddesh_core/api/v1/incidents.py`
- `core/zaddesh_core/api/v1/infrastructure_monitoring.py`
- `core/zaddesh_core/api/v1/logs.py`
- `core/zaddesh_core/api/v1/metrics.py`
- `core/zaddesh_core/api/v1/network_diagram.py`
- `core/zaddesh_core/api/v1/system.py`
- `core/zaddesh_core/api/v1/workstations.py`
- `core/zaddesh_core/api/v1/zabbix.py`
- `core/zaddesh_core/config.py`
- `core/zaddesh_core/database.py`
- `core/zaddesh_core/email_worker.py`
- `core/zaddesh_core/incident_delivery.py`
- `core/zaddesh_core/incidents.py`
- `core/zaddesh_core/integration_logs.py`
- `core/zaddesh_core/main.py`
- `core/zaddesh_core/models/__init__.py`
- `core/zaddesh_core/models/alerts.py`
- `core/zaddesh_core/models/infrastructure.py`
- `core/zaddesh_core/models/workstations.py`
- `core/zaddesh_core/mongo_queries.py`
- `core/zaddesh_core/mongo_store.py`
- `core/zaddesh_core/schemas/device.py`
- `core/zaddesh_core/seed.py`
- `core/zaddesh_core/storage_init.py`
- `core/zaddesh_core/topology.py`
- `core/zaddesh_core/workstation_logs.py`
- `scripts/audit/browser.py`
- `scripts/dev.ps1`
- `scripts/dev.sh`
- `scripts/dev_stack.py`
- `scripts/migrate_to_mongodb.py`
- `web/e2e/monitoring.spec.ts`
- `web/e2e/zabbix.spec.ts`
- `web/src/App.tsx`
- `web/src/agents/enable-services-linux.sh`
- `web/src/agents/enable-services-windows.ps1`
- `web/src/agents/events-linux.py`
- `web/src/agents/events-windows.ps1`
- `web/src/agents/includeEventLogInstaller.test.ts`
- `web/src/agents/includeEventLogInstaller.ts`
- `web/src/agents/setup-linux-snmp.sh`
- `web/src/agents/setup-windows-snmp.ps1`
- `web/src/components/layout/Sidebar.tsx`
- `web/src/components/ui/InfrastructureMonitoring.tsx`
- `web/src/components/ui/OverviewLatency.tsx`
- `web/src/components/ui/OverviewSummary.tsx`
- `web/src/components/ui/SnmpCheckForm.tsx`
- `web/src/components/ui/WorkstationInventory.tsx`
- `web/src/components/ui/formatMetricValue.ts`
- `web/src/pages/Devices.tsx`
- `web/src/pages/Logs.tsx`
- `web/src/pages/Overview.tsx`
- `web/src/services/api.ts`
- `web/src/types/index.ts`

Final legacy regression: 86 passed, 15 skipped (14 native tests executed separately; one pre-existing skip). Native suite: 14 passed.

## 2026-11-18: Critical-alert inflation and diagram refresh regression

Reproduced 1,045 critical incidents: SNMP interface counter failures were classified as device-offline merely because the local device status was down. Imported hosts now use the Zabbix incident feed for availability, avoiding duplicate local probe incidents. Scheduled scalar/OID failures remain telemetry/log events rather than host-outage alerts. Default sysUpTime reachability and on-demand SNMP snapshots retain outage handling; resource and uplink classifications remain available. Historical records were not deleted.

Reproduced diagram dependencies: graph reads returned 200, but fetching 1,000 SNMP checks took 9.4 seconds. Frequent metric-driven WebSocket invalidations restarted requests. Added a credential-free discovery-seed endpoint returning one check per device, replaced the full-check download, and coalesced live refresh notifications to at most one per ten seconds.

Live verification: native critical count decreased from 1,045 to zero false incidents; the separately sourced Zabbix feed remains authoritative. All three diagram graph endpoints returned 200. Chrome rendered 49 SVG groups, still present after twelve seconds of live updates. Screenshot: `.audit/ui-polish/diagram-regression-fixed.png`.

Validation: 17 incident/topology tests passed; the real MongoDB imported-host filtering regression passed and confirmed genuine CPU incidents remain visible. TypeScript, ESLint, production build, Ruff and mypy passed. The broader native suite was attempted but failed while importing `scripts/migrate_to_mongodb.py`: unexpected non-source content caused a SyntaxError. This maintenance-tool failure remains open and is not a full-suite pass; do not run another database migration until that source/integrity issue is repaired. It does not participate in serving the live dashboard.

## 2026-11-18: GlassWire-inspired live probe latency

The latency panel now uses translucent overlapping areas, smooth monotone curves, a green/lavender/cyan palette, per-device keys, and light/dark themes. This is a reference-inspired monitoring chart, not fabricated traffic. Missing samples remain gaps, failed probes stay in a separate red strip, and hover/export retain real units and timestamps. A labelled five-minute live range makes newly collected data visible immediately; 15 minutes and the shared dashboard range remain selectable.

Fixed refresh starvation: latency requests previously depended on the Overview snapshot timestamp, so frequent live updates could cancel a pending request. The panel now loads immediately, independently of the events feed, and schedules its next request ten seconds after completion. Changes to device/protocol/range still cancel obsolete requests.

Live diagnosis: the original one-hour API response returned HTTP 200 in 2.7 seconds, with 24 priority devices but only 14 configured ICMP channels, 12 successful samples and 766 failures. Direct Windows ping confirmed one registered switch timed out while a registered server answered in 2 ms. Ten existing priority, monitored, scheduled devices lacked ICMP checks. Added one real ICMP reachability check per missing device via the authenticated API (30-second interval, one packet, 1.5-second packet timeout). No on-demand workstation configuration was changed and no historical samples were invented.

After collection, the API returned 24 channels and 71 successful samples. Chrome confirmed the canvas renders, stays visible, and completes another successful refresh during live updates. Both theme screenshots were inspected: `.audit/ui-polish/glass-latency-dark.png` and `glass-latency-light.png`. Some real targets still fail ICMP and remain visibly marked; enabling a graph does not make an unreachable network target reachable.

Validation: TypeScript, ESLint, production build and ten Vitest tests passed. A new Playwright regression injects a 3.5-second telemetry response and live change events every 2.1 seconds; the chart still finishes loading (1 passed). The artificial latency series is confined to that browser test. The deployed graph uses real collected data.

## 2026-11-18: SNMP hardware vendor and model

Bug HW-001 (Medium, fixed in `6f13005`): Zabbix device rows rendered a literal dash in Vendor & Model. The integration also omitted host hardware inventory and system identity items; local SNMP topology observations never populated Device.vendor/model. The Devices table now displays reported hardware, falls back to the matched local record, and explicitly labels missing identities. The column has enough width for readable model names.

Added a shared hardware resolver using ENTITY-MIB chassis manufacturer/model, sysDescr, sysObjectID enterprise identity, and verified Cisco product identifiers. The collector now walks physical class/manufacturer/model objects for topology and workstation snapshots. Successful snapshots fill missing local hardware fields while preserving existing manual values. Zabbix imports persist resolved hardware; inventory uses one bounded, cached item lookup for all returned hosts rather than per-device requests. Operating-system and generic SNMP-agent names are not presented as hardware manufacturers.

Product mapping sources: [Cisco product OIDs](https://github.com/cisco/cisco-mibs/blob/main/v2/CISCO-PRODUCTS-MIB.my) and [Cisco physical entity OIDs](https://github.com/cisco/cisco-mibs/blob/main/v2/CISCO-ENTITY-VENDORTYPE-OID-MIB.my). The Catalyst 37xx stack identity describes the stack family, not an invented member SKU.

Live evidence: authenticated `GET /api/v1/zabbix/inventory` returned HTTP 200 with 25 hosts, 12 reporting vendors and 12 resolved model/family identities, including two N3K-C3064PQ-10GX core switches. Twelve matched local records received missing identity data in MongoDB. Thirteen hosts still have no usable hardware identity in current upstream readings; the UI states "Vendor not reported" / "Model not reported". These require a responding SNMP hardware inventory or populated upstream hardware items; their manufacturer cannot truthfully be inferred from Windows/Linux descriptions alone. No demo identities were inserted.

The initial backfill encountered MongoDB WriteConflict twice while collection was active, including with short per-device transactions. It completed successfully while the owned stack was stopped for deployment. The collector spool was preserved, and both the API readiness endpoint and UI URL returned 200 after restart. This does not establish that general database write contention is resolved.

Validation commands/results:

- `pytest core/tests/test_hardware_identity.py core/tests/test_zabbix.py core/tests/test_network_diagram.py -q`: 20 passed. Covers exact product OIDs, chassis versus module selection, ambiguous/absent data, OS-versus-hardware identity, import persistence, and topology persistence.
- Ruff on changed Python modules/tests and mypy on the core: passed.
- Collector `cargo clippy --locked -- -D warnings`, `cargo fmt -- --check`, `cargo test --locked` (4 passed), `cargo build --release --locked`: passed.
- Frontend TypeScript, ESLint, Vitest (10 passed), production build: passed.
- `playwright test e2e/hardware-identity.spec.ts`: 1 passed with browser-only API fixtures. Its first run failed because unrelated endpoints returned invalid fixture shapes; corrected those responses and reran successfully.
- Separate Chrome smoke check against live data confirmed two exact Nexus model cells and 13 explicit missing-model labels. Screenshot inspected: `.audit/ui-polish/hardware-identity-devices.png`. Live response summary: `.audit/local-run/hardware-http-result.json`.

Changed files in `6f13005`: collector SNMP probes; core hardware_identity resolver, Zabbix/workstation API handlers and topology persistence; hardware/Zabbix/topology tests; UI Zabbix types and Devices table; browser hardware identity regression. Linux execution and the broader previously blocked migration/native suite were not verified by this targeted change. Both deployed local UI paths contain the updated build.

## 2026-11-18: SNMP portability beyond the current installation

Implemented in `22a9e0f`. Discovery now handles arbitrary reported manufacturer/model strings through standard ENTITY-MIB, labelled physical classes, leading-dot numeric OIDs, heterogeneous chassis, and stack identities. Fixed a single known module being eligible as the chassis fallback; conflicting chassis models no longer select whichever row happens to arrive first. Zabbix identity lookup also searches and reads numeric SNMP OIDs, allowing custom item names and selecting physical rows by class. The collector preserves multiline textual descriptions instead of converting them to binary hex, while retaining binary identifier handling and bounded string lengths.

Product and enterprise mappings can now be extended through validated `snmp_identity` YAML/environment configuration without changing application code. Defaults are empty extensions; the example catalog in the guide is illustrative only. No current deployment IPs were found in core runtime, collector runtime, UI source or default configuration by `rg` for the current target subnets. Existing user hardware fields remain protected from automatic replacement.

Validation: `pytest core/tests/test_hardware_identity.py core/tests/test_zabbix.py core/tests/test_network_diagram.py` passed 27 tests; Ruff and core mypy passed. Collector Clippy with warnings denied, format check, five Rust tests, and release build passed. Cross-vendor tests are explicitly synthetic fixtures, including an unlisted manufacturer; this is not physical certification of Juniper, Aruba, Ubiquiti, every SNMP version, or every operating system. Frontend source did not change in this follow-up.

Deployment: restarted the owned API/collector with the built binary and preserved spool. Final live inventory returned HTTP 200, 25 hosts, and 12 vendor/model identities, retaining the prior real-data coverage. Readiness and the UI both returned 200. Evidence: `.audit/local-run/snmp-portability-tests.log`, `.audit/local-run/portable-snmp-live.log`.

See [SNMP portability and rollout guide](docs/snmp-portability.md), including standard OIDs, configuration extension examples and verification limits. Identity support cannot manufacture data an agent omits or disallows; vendor-private resource metrics still require suitable templates. Another network's credentials, ACLs, supported MIBs and agent behavior require rollout validation. The existing README could not be patched through the UTF-8 editor because it contains an invalid UTF-8 byte near offset 11208; that pre-existing encoding issue is left unchanged, and the guide is supplied as a separate UTF-8 document.

## 2026-11-18: Critical SNMP errors, priority and notifications

Full device-wise report and all **127 supplied requirements**: [SNMP Critical Audit](docs/SNMP_CRITICAL_AUDIT.md). The matrix separates existing verified logic, corrected communication/link behavior, newly added conditional agent-state rules, explicitly configured scalar/rate policies, and unsupported native detections. No blanket claim of all-device/all-error support is made.

Audit first: 24 existing incident/email/topology tests passed. Found and fixed absent native AlertRule evaluation, absent native SMTP routing, SNMP timeout/device-offline confusion, unsupported-object health misreporting, Critical view exclusion of high-impact hardware/VPN faults, and access-port/uplink severity handling. Added eight Cisco/Net-SNMP documented agent-state families and optional read-only collection within existing scan budgets. Reused current Incident, Alert, MetricRaw, EmailProblem and EmailDelivery records and workers; no UI, model, migration or permission changes.

Commits `3c41c8c` and `29fddd7`. Tests: 134 core tests passed with one skip, five selected real MongoDB tests passed, seven Rust tests passed, ten frontend unit tests and TypeScript passed. Ruff, mypy, Clippy, formatting and release build passed. Protocol tests used actual loopback SNMPv2c exception PDUs; SMTP tests delivered real opening/recovery emails to a loopback receiver with retry and deduplication. Native intake beyond 200 episodes passed. The initial SMTP recovery test attempted a send before the existing send interval; corrected the test timing and reran successfully. Type/lint failures found during implementation were corrected before final checks.

Deployment verified: installed collector matches release binary; health/readiness/UI and native/Zabbix Critical Errors and network-diagram endpoints returned 200. Initial Windows executable locking was resolved by stopping only the owned collector before copying. Captured hashes for 24 devices, 5,580 checks, 13 credential records, saved rule/notification configurations and user permissions matched after deployment. Database collection list was unchanged. Collector spool and existing data were preserved.

Remaining limitations: no active native trap receiver exists; proprietary camera/NVR/HA/VPN/RAID/security event adapters are not invented. Physical multi-vendor certification, complete native migration regression and Linux execution were not performed. The live database has zero native threshold rules and zero SMTP settings/rules/recipients; those were preserved, so native custom policy and external email remain unconfigured despite passing controlled tests. Existing upstream triggers retain their own policy. Cross-source semantic deduplication of unrelated native/Zabbix event identifiers is not established. Details, per-item conditions, capacity limits and measured live timings are in the report.

## 2026-11-18: MongoDB process outage and recovery

The user-facing storage-unavailable message corresponded to ServerSelectionTimeoutError across login, ingestion and background jobs. No mongod process was running. The MongoDB log recorded a fatal **out of memory** message at **17:26:19 Asia/Karachi**, in the diagnostic collector thread. This establishes the immediate shutdown reason, not which allocation or application ultimately caused memory exhaustion. Disk space was available (approximately 35.8 GB on C and 133.2 GB on E).

Restarted the existing authenticated replica-set instance through the saved local launcher, using its original data directory and credentials. The existing API reconnected without needing a new database or application rewrite. MongoDB private memory was about 616 MB after recovery. The 16 GB host reported approximately 2.4 GB physical memory and 6.3 GB virtual/commit headroom free at the sampled time; memory pressure remains a recurrence risk. No unrelated applications were terminated and no operating-system memory settings were changed.

Recovery evidence: login initialization, readiness, native/Zabbix Critical Errors and network diagram requests returned HTTP 200. Collector heartbeats returned 200 and metric batches resumed HTTP 202. During the outage, collector logs explicitly recorded preservation of rejected spooled batches. Saved configuration/credential/permission hashes and database collection names still matched the prior snapshot (24 devices, 5,580 checks). No destructive database recovery was performed. This restores service; it does not prove the underlying memory-exhaustion condition is permanently resolved.
