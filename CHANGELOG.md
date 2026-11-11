# Changelog

## [Unreleased]

- Added the opt-in Alerts, Events and Logs workspace with correlated probe incidents, a timeline and webhook delivery.
- Added the opt-in Network Diagram Beta with Admin-only SNMPv3 seed discovery and published read-only Overview widgets.
- See [the add-on guide](ADDON_USER_GUIDE.md) and [verification report](ADDON_FEATURES_REPORT.md).

## [0.1.0]

First tracked ZadDesh baseline.

- Infrastructure dashboard with device inventory, fleet latency, alerts and events.
- Rust collector with ICMP, HTTP(S), TCP, and SNMP polling. Default SNMPv3 provisioning includes Windows and Linux setup scripts.
- Administrator account management and read-only User access.
- MongoDB-backed infrastructure logs and UDP syslog forwarding. The primary database uses SQLite by default.
- Cross-platform CI, database migrations, and local audit tools.

See [AUDIT_REPORT.md](AUDIT_REPORT.md) for verification results and remaining gaps.
