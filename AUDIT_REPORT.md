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
