# Changelog

## [Unreleased]

- Added the opt-in Alerts, Events and Logs workspace with correlated probe incidents, a timeline and webhook delivery.
- Added a monitoring Errors view and improved expandable messages, log navigation, and load failure handling.
- Made Critical Errors the default workspace view and added date-range removal, plus automatic removal from current views on resolution or after eight hours.
- Added a read-only Zabbix connection with encrypted token storage, host and problem visibility, and ICMP reachability validation.
- Allowed HTTP Zabbix connections and preferred Zabbix rows for matching IPs in the Devices list.
- Added per-host import from Zabbix with supported ICMP and SNMP check settings.
- Displayed active Zabbix agent availability and recent reporting in Devices, and surfaced Zabbix problems and events in Alerts & Events.
- Aligned Overview incident counts and fleet health with Critical Errors and the combined device inventory.
- Added the opt-in Network Diagram Beta with Admin-only SNMPv3 seed discovery and published read-only Overview widgets.
- Network Diagram Beta now builds and refreshes an automatic map from saved SNMPv3 checks and preserves Admin edits.
- See [the add-on guide](ADDON_USER_GUIDE.md) and [verification report](ADDON_FEATURES_REPORT.md).

## [0.1.0]

First tracked ZadDesh baseline.

- Infrastructure dashboard with device inventory, fleet latency, alerts and events.
- Rust collector with ICMP, HTTP(S), TCP, and SNMP polling. Default SNMPv3 provisioning includes Windows and Linux setup scripts.
- Administrator account management and read-only User access.
- MongoDB-backed infrastructure logs and UDP syslog forwarding. The primary database uses SQLite by default.
- Cross-platform CI, database migrations, and local audit tools.

See [AUDIT_REPORT.md](AUDIT_REPORT.md) for verification results and remaining gaps.
