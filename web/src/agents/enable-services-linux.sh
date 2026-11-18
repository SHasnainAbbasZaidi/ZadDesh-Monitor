#!/usr/bin/env bash
# Run with sudo on an existing ZadDesh Net-SNMP client.
set -euo pipefail
[[ $EUID -eq 0 ]] || { printf 'Run with sudo.\n' >&2; exit 1; }
configuration=/etc/zaddesh-snmp/agent.conf
[[ -f $configuration ]] || { printf 'Install the ZadDesh Net-SNMP agent first.\n' >&2; exit 1; }
exec 9>/run/lock/zaddesh-snmp.lock
flock -n 9 || { printf 'Another agent setup is running.\n' >&2; exit 1; }
backup=$(mktemp /etc/zaddesh-snmp/agent.conf.XXXXXX)
cp -- "$configuration" "$backup"
restore() { cp -- "$backup" "$configuration"; rm -f -- "$backup"; systemctl restart zaddesh-snmp.service || true; }
trap restore ERR
cat > /etc/zaddesh-snmp/services.sh <<'SERVICES'
#!/bin/sh
if command -v systemctl >/dev/null 2>&1; then
    systemctl list-units --type=service --all --no-pager --no-legend --plain 2>/dev/null |
        awk 'NF >= 4 { printf "%s\t%s\n", $1, $4 }'
elif command -v rc-status >/dev/null 2>&1; then
    rc-status --all --nocolor 2>/dev/null | awk '/\[/ {gsub(/\[|\]/, ""); printf "%s\t%s\n", $1, $2}'
fi
SERVICES
chmod 0755 /etc/zaddesh-snmp/services.sh
# ZADDESH EVENT LOG INSTALL
sed -i '/^[[:space:]]*extend[[:space:]]\+zaddesh_services[[:space:]]/d' "$configuration"
printf 'extend zaddesh_services /bin/sh /etc/zaddesh-snmp/services.sh\n' >> "$configuration"
systemctl restart zaddesh-snmp.service
trap - ERR
rm -f -- "$backup"
printf 'Service inventory enabled. Refresh the SNMP snapshot in ZadDesh.\n'
