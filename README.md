# ZadDesh

ZadDesh is an early-stage self-hosted infrastructure monitor. It has a FastAPI core, a React UI, a Rust collector for ICMP, HTTP(S), TCP, and SNMP polling, and a constrained Ruby rule evaluator. The default database is MongoDB; the core uses native MongoDB queries and transactions. See [AUDIT_REPORT.md](AUDIT_REPORT.md) for the verification results and known gaps. SNMP traps, generic UDP, notification delivery, scheduled rule execution, and rollup jobs are unavailable.

## Local quick start

Install Python 3.11 or newer, Node.js 24 with npm, and an authenticated MongoDB 8 replica set. Set `ZADDESH_DATABASE__URL` to the application connection URI, with the `zaddesh` database and replica-set name. Grant its account read/write access to `zaddesh` and `zaddesh_logs`. Run the following from the repository root. The launchers install the core if needed, initialize MongoDB collections and indexes, and bind to loopback. Build the UI explicitly when upgrading an existing checkout. Replica sets provide the transactions used for consistent ingestion and alert updates. A single-member replica set supports a local installation.

**Windows (PowerShell)**

```powershell
.\scripts\dev.ps1 -BuildUI
.\scripts\dev.ps1
```

If Python is not on PATH, append `-Python C:\path\to\python.exe`. Use `-Port 8001` to choose another local port.

**Linux (Bash)**

```bash
bash scripts/dev.sh build-ui
bash scripts/dev.sh
```

On Debian/Ubuntu, install `python3-venv` for a new environment. Set `PYTHON` to choose a different Python executable. Set `ZADDESH_PORT` to choose another port.

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), create the first administrator, then sign in. `/healthz` checks the process and `/readyz` checks storage connectivity. MongoDB stores accounts, configuration, devices, checks, metrics, alerts, topology, notifications and logs. Core timestamps preserve microsecond precision as UTC integer microseconds, with normal datetime values returned by the API. Keep the signing and encryption keys persistent across restarts and migrations.

## Collect real samples

Build the collector with a current stable Rust toolchain and the platform's C toolchain. On Windows, install MSVC Build Tools with the C++ workload, or use the GNU Rust host toolchain with MinGW-w64 on PATH.

```text
cd collector
cargo build --release --locked
cd ..
```

Set these environment variables in your local launch shell. Use independent random values of at least 32 characters:

| Variable | Purpose |
| --- | --- |
| `ZADDESH_COLLECTOR__ENROLLMENT_TOKEN` | Core enrollment credential. Unset after enrollment to close registration. |
| `ZADDESH_COLLECTOR_ENROLLMENT_TOKEN` | Same enrollment credential, supplied to the Rust collector for the first run only. |
| `ZADDESH_COLLECTOR_TOKEN` | Unique collector token, persisted by the operator; the core stores its hash. |
| `ZADDESH_SECURITY__JWT_SECRET` | Optional for one process. Configure a persistent random signing key if sessions must survive restart. |
| `ZADDESH_SECURITY__ENCRYPTION_KEY` | Required for reversible credential encryption and demo seeding. Keep it separate from the signing key. |

Launch the core, built SPA, and collector in one command, once dependencies and binaries are installed:

```powershell
.\.venv\Scripts\python.exe scripts\dev_stack.py --collector collector\target\release\zaddesh-collector.exe
```

```bash
.venv/bin/python scripts/dev_stack.py --collector collector/target/release/zaddesh-collector
```

On enrollment, the collector prints its assigned ID. For later starts, unset both enrollment variables and set `ZADDESH_COLLECTOR_ID` to that ID. Reuse a distinct spool directory per collector, with write access for the collector user. For smoke tests only, a separate `ZADDESH_COLLECTOR__SHARED_SECRET` can grant fleet-wide ingestion access; it defaults to disabled.

As an administrator, use **Devices â†’ Add Device** and choose initial checks. Defaults are ICMP, HTTP port 80, and TCP port 80 for selected protocols. The API can set custom HTTP URLs or TCP targets/ports. Operators can triage incidents; probe configuration requires administrator access because collectors reach internal networks. The API currently uses one shared inventory across sites; roles do not form separate tenant boundaries.

This launcher is for local use. For remote evaluation, complete setup locally before configuring a TLS reverse proxy, enforce request size/rate limits there, and limit collector egress to approved networks. The Ruby evaluator is excluded because it does not run a notification or scheduling service.

### SNMP polling

Sign in as an admin and go to **Devices → Add Device**. Enter the device IP and, for DNS polling, its hostname. Select **SNMP** to expand the interface, then choose IP or DNS, the UDP port, and SNMPv1, v2c, or v3. Enter the community for v1 or v2c, or the security name and the passphrases required by the v3 security level. Set the operation and numeric OID, then click **Provision Device**. Numeric samples appear in the device’s telemetry view after the collector polls it.

Direct entry requires a persistent `ZADDESH_SECURITY__ENCRYPTION_KEY` and an enrolled collector with its own token. ZadDesh encrypts the credentials at rest and requires HTTPS for remote credential submission and delivery. Direct loopback access is supported by the local dev stack. Keep the encryption key across restarts and backups. Select **Collector alias** to keep credentials in the collector’s environment. For an existing device, **Checks → Add SNMP Check** uses this alias workflow.

For a new client, choose **Default SNMP** in **Devices → Add Device → SNMP**. Enter the client IPv4 address and UDP port, then download the platform setup script. Keep the dialog open until you click **Provision Device**. ZadDesh generates separate authentication and privacy passphrases for each device. Transfer the script securely and delete it from both machines after setup because it contains those passphrases. The profile uses SNMPv3 authPriv, security name `zaddesh`, SHA-1, AES-128, and a read-only uptime check.

If the browser blocks the download, expand **Download didn’t start? Copy setup script** and save the script with the displayed filename. Open ZadDesh over HTTPS or direct loopback for default SNMP provisioning.

- **Windows** requires an approved Net-SNMP build for the client OS and architecture with SNMPv3 cryptography and Windows service support. Run `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\setup-windows-snmp.ps1` as Administrator. The script finds `snmpd.exe` or prompts for its path, configures the service, and adds a firewall rule for the collector IPv4 address you enter. Live installation across Windows 7, 8, 10, 11 and Server 2012–2025 remains unverified.
- **Debian or Ubuntu with systemd** runs `sudo bash ./setup-linux-snmp.sh`. The script installs Net-SNMP if needed and starts a dedicated service on the client IPv4 address and UDP port from the profile. Allow that UDP port from the collector in the client firewall.

Choose **Custom SNMP settings** for an existing agent or to choose SNMP version, credentials, OID, operation or other settings yourself.

For an alias-backed check, use `params` such as `{"credential":"switch_a","version":"3","security_level":"authPriv","auth_protocol":"sha1","privacy_protocol":"aes128","operation":"get","oid":"1.3.6.1.2.1.1.3.0"}`. The default port is 161. Provide `ZADDESH_SNMP_SWITCH_A_USERNAME`, `ZADDESH_SNMP_SWITCH_A_AUTH_PASSWORD` and `ZADDESH_SNMP_SWITCH_A_PRIV_PASSWORD` in the collector's private environment. Use `ZADDESH_SNMP_SWITCH_A_COMMUNITY` for v1/v2c. Prefer v3 with authentication and privacy on real networks.

### User accounts

Sign in as an **Admin** and open **Settings → Users and access** to create accounts, change access levels, enable or disable other accounts, and set passwords. Admins can change their own password by entering the current one. Password and access changes invalidate the affected account’s sessions and API tokens.

**Admin** has full access to devices, checks, incident actions, and Settings. **User** has read-only access to Overview, Infrastructure Logs, and Alerts & Events. Existing non-admin roles are treated as User accounts.

### Centralized infrastructure logs

Infrastructure Logs has separate **ZadDesh logs** and **Integrated app logs** sections. ZadDesh events and imported logs use MongoDB. Connected Zabbix events are archived automatically regardless of device alert priority. Other applications and log shippers can forward records to the ingestion API with a provider label and device ID. The shared application MongoDB URI is used by default. `ZADDESH_LOGS__MONGO_URI` can select a dedicated MongoDB log deployment. Set a random secret of at least 32 characters in `ZADDESH_LOGS__INGEST_TOKEN` before accepting forwarded logs. `ZADDESH_LOGS__DATABASE` selects the log namespace and `ZADDESH_LOGS__RETENTION_DAYS` controls the default 30-day retention.

Signed-in users can filter entries by source, application and severity. Log shippers POST to `/api/v1/logs/ingest` using the dedicated bearer token over HTTPS or direct loopback. The request body has an `entries` array with up to 100 objects. Each object accepts `source`, `message`, `severity`, optional `provider` and `device_id`, an optional timezone-aware `timestamp`, and an optional UUID `event_id`. Provider IDs include `zabbix`, `grafana`, `prometheus`, `solarwinds`, `graylog`, `loggly`, `logzio` and `opentelemetry`. Associate workstation logs with its numeric `device_id` to show them in device details. Reuse the same UUID when retrying a batch.

To receive UDP syslog from appliances, run the forwarder from the Python environment used by the core. Set the same ingest token in its environment and substitute the receiver's LAN address and your trusted source subnet.

```bash
python scripts/syslog_forwarder.py --bind 192.0.2.20 --port 5514 --allow 192.0.2.0/24 --server http://127.0.0.1:8000
```

Configure devices to send UDP syslog to the receiver address and port. Run the forwarder on a trusted network segment. UDP syslog offers no sender authentication or delivery guarantee, and the forwarder buffers pending batches in memory.

To migrate an existing installation, stop the old core and collector, preserve its signing and encryption keys, and set `ZADDESH_DATABASE__URL` to the old SQLite URL temporarily. Upgrade the offline source with `python -m alembic upgrade head` from `core`. Set `ZADDESH_MIGRATION_MONGO_URI` to an application URI for an empty MongoDB target, then run from the repository root:

```bash
python scripts/migrate_to_mongodb.py --source core/zaddesh-live.db --report migration-report.json
```

For a large installation, first migrate a consistent SQLite backup while the existing app runs. Stop the old stack, upgrade its final SQL source to the same schema, then repeat the command with `--catch-up` to reconcile configuration and append immutable metrics written after the backup. This mode supports the app�s device/check deletion paths and rejects mismatched record counts. Keep both sources until validation finishes.

The source is opened read-only. Every batch is read back from MongoDB and compared, then collection record counts are verified. IDs, timestamps, password hashes and credential ciphertext are preserved. After verification, set `ZADDESH_DATABASE__URL` to the target MongoDB URI, keep the original encryption and signing keys, and launch the stack. Existing infrastructure devices retain priority. Newly added devices default to non-priority.

### SNMP workstations and priority

**Discovered Devices** lists registered devices with enabled SNMP interfaces. Its add-device form provisions SNMP and defaults to on-demand collection. Opening device details requests a process, service and resource snapshot from the collector. The collection selector switches between on-demand SNMP and scheduled telemetry. The snapshot timestamp identifies when data was observed.

Select **Priority alerts** in either device inventory or device details to include that device in Overview, Alerts & Events and incident notifications. Non-priority device telemetry and logs remain available in device details. Logs from both origins are retained centrally.

Process snapshots use HOST-RESOURCES-MIB. Service snapshots use the `zaddesh_services` Net-SNMP extension installed by the current Windows and Linux command scripts. Existing clients can download the services and issue-log update scripts from device details. The read-only `zaddesh_events` extension retrieves the latest 30 Windows System/Application warnings and errors, or Linux system-journal issues, when a snapshot is requested. Records keep their original timestamps and are deduplicated across snapshots. Linux requires Python 3 and system-journal read access; Windows uses the Event Log Readers group. Missing permissions and collection limits are shown in details. This is on-demand retrieval, so older events can rotate out of the client before retrieval. Agents that do not expose a table show an unavailable state. Inventory collection uses GETNEXT for SNMPv1 and GETBULK for v2c/v3, with bounded table sizes and a 55-second collection deadline.

Supported operations are GET, GETNEXT, GETBULK (v2c/v3) and a bounded 64-object WALK. Numeric samples appear under `snmp.<numeric OID>` and in the device telemetry view. Counter rates, traps and MIB name lookup are separate, unavailable features.

## Email Alerts & Notifications

Administrators can configure the SMTP server, add employees and team memberships, and assign Zabbix notification rules under **Settings → Email Alerts & Notifications**. Save the SMTP settings, test the connection, and send a marked test message before enabling automatic notifications. SMTP credentials use the core encryption key. Set `ZADDESH_SECURITY__ENCRYPTION_KEY` to a persistent secret and use HTTPS or local loopback when entering credentials. Keep STARTTLS or SSL/TLS enabled when authenticating. XOAUTH2 accepts a valid provider-issued access token and requires a replacement when that token expires.

Use the **Time zone** field for quiet hours, employee schedules, and daily history counts. The background worker begins with Zabbix trigger events created since automatic notifications were enabled. It sends recovery emails only after Zabbix confirms a recovery event, to eligible recipients whose earlier message was SMTP-accepted. **Skip recipients already queued or notified by native Zabbix email** uses the saved token's alert and media-type permissions. The worker delays affected mail when it cannot verify that check.

In **Alert Rules & Conditions**, use **Switch ports** to restrict a rule to monitored interfaces. Select **Link down** and the severities your Zabbix templates use for uplink outages. The worker identifies the interface from the original trigger's items. A rule with port selections requires a single identifiable interface, so triggers that cover multiple ports need a broader rule.

SMTP-accepted status records the receiving mail server's response. Inspect SMTP server logs before manually retrying an interrupted or ambiguous attempt. Notification history is retained for 90 days and pending delivery is capped at 2,000 messages. Run the core's Alembic migrations before starting an upgraded installation.

## Overview infrastructure monitoring

The Infrastructure Device Monitoring section uses the saved Zabbix connection. It shows a usage estimate from fresh CPU, memory, and interface measurements, plus a separate health estimate based on confirmed availability, monitoring coverage, and problems. Detailed history is available to administrators. Category overrides are saved per user in the current browser.

The core accepts these optional environment variables for this section.

- `ZADDESH_INFRA_REFRESH_SECONDS` sets the summary cache and refresh interval. The default is 25 seconds, with a supported range of 15 to 300 seconds.
- `ZADDESH_INFRA_NETWORK_POLICY` accepts `busiest` (default) or `uplinks`.
- `ZADDESH_INFRA_WEIGHTS_NETWORK`, `ZADDESH_INFRA_WEIGHTS_SERVERS`, `ZADDESH_INFRA_WEIGHTS_ROUTERS`, `ZADDESH_INFRA_WEIGHTS_FIREWALLS`, `ZADDESH_INFRA_WEIGHTS_CCTV`, and `ZADDESH_INFRA_WEIGHTS_OTHER_DEVICES` accept a JSON object such as `{"cpu":40,"memory":30,"network":30}`. Missing measurements are excluded before normalizing weights.
- `ZADDESH_INFRA_PENALTIES` accepts a JSON object such as `{"hardware":35,"uplink":25,"errors":15,"pressure":10,"warning":5}`.

## Isolated demo

Set `ZADDESH_DEMO_MODE=true`, `ZADDESH_DATABASE__URL` to a separate MongoDB database named `zaddesh_demo`, and a unique `ZADDESH_DEMO_PASSWORD` of at least 16 characters. Run `scripts\dev.ps1 -Seed` on Windows or `bash scripts/dev.sh seed` on Linux, then start the core with the same demo mode and database settings. Sign in as `admin` with the password you set. The dashboard labels the sample data and collection is disabled. To return to live monitoring, unset the demo settings and launch the core again. Seeding does not overwrite a populated database.

## Verification

Run `.\scripts\dev.ps1 -Test` or `bash scripts/dev.sh test` for the core tests. In `web/`, run `npm run lint`, `npm run typecheck`, and `npm test`. Install Chromium with `npx playwright install chromium` before running the disposable browser harness; set `ZADDESH_BROWSER` to an installed Chromium executable to use it instead.

```text
python scripts/audit/browser.py --db new-browser-lab.db
python scripts/audit/collector_lab.py --collector PATH_TO_BINARY --work NEW_LAB_DIRECTORY
python scripts/audit/load_core.py --db new-load-lab.db --rounds 31 --interval 60
```

Run these commands with the project virtual environment active. The load generator inserts synthetic telemetry; it measures the core and SQLite, not Rust probe capacity. On Linux, install `psutil` in the audit virtual environment and pass `--resources` to collect core process samples. OpenAPI is served at `/openapi.json` and the interactive API at `/docs`.

## Versioning

Use [Semantic Versioning](https://semver.org/) and annotated Git tags such as `v0.1.0`. Keep a changelog entry for each tagged version. Before tagging a new version, update the Python, Rust, Ruby, and web version declarations, their lockfiles where applicable, and the versions shown in the API and UI. Run the relevant checks, then tag the release commit.

```bash
git tag -n
git log --oneline v0.1.0..HEAD
```
