#!/usr/bin/env bash
# Run with sudo on a Debian or Ubuntu client using systemd.
set -euo pipefail
umask 077
# ZADDESH GENERATED PROFILE

fail() { printf '%s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || fail 'Run this script with sudo.'
command -v apt-get >/dev/null && [[ -d /run/systemd/system ]] ||
    fail 'This installer requires Debian or Ubuntu with systemd.'

config_dir=/etc/zaddesh-snmp
state_dir=/var/lib/zaddesh-snmp
unit=/etc/systemd/system/zaddesh-snmp.service
service=zaddesh-snmp.service
exec 9>/run/lock/zaddesh-snmp.lock
flock -n 9 || fail 'Another ZadDesh SNMP setup is running.'
for path in "$config_dir" "$state_dir" "$unit"; do
    [[ ! -e $path && ! -L $path ]] || fail "Setup already exists at $path."
done

: "${agent_port:?Download a device-specific setup script from ZadDesh.}"
: "${listen_ip:?Missing client IP in setup script.}"
[[ $agent_port =~ ^[0-9]{1,5}$ ]] && (( agent_port >= 1 && agent_port <= 65535 )) || fail 'Invalid UDP port.'
ip -4 -o addr show | awk '{split($4, address, "/"); print address[1]}' |
    grep -Fx -- "$listen_ip" >/dev/null || fail 'The configured IPv4 address is not assigned to this client.'
for passphrase in "$auth" "$privacy"; do
    [[ $passphrase =~ ^[0-9a-f]{32}$ ]] || fail 'Invalid generated SNMPv3 passphrase.'
done

if [[ ! -x /usr/sbin/snmpd ]]; then
    apt-get update
    apt-get install -y snmpd
fi
getent passwd Debian-snmp >/dev/null || fail 'The Debian-snmp service account is missing.'

rollback() {
    systemctl disable --now "$service" >/dev/null 2>&1 || true
    rm -f -- "$unit" "$config_dir/agent.conf" "$config_dir/services.sh" "$config_dir/events.py" "$state_dir"/snmpd*.conf
    rmdir -- "$state_dir/cert_indexes" 2>/dev/null || true
    rmdir -- "$config_dir" "$state_dir" 2>/dev/null || true
    systemctl daemon-reload >/dev/null 2>&1 || true
}
trap rollback EXIT
trap 'exit 1' HUP INT TERM
install -d -m 0755 "$config_dir"
install -d -m 0700 -o Debian-snmp -g Debian-snmp "$state_dir"
cat > "$config_dir/services.sh" <<'SERVICES'
#!/bin/sh
if command -v systemctl >/dev/null 2>&1; then
    systemctl list-units --type=service --all --no-pager --no-legend --plain 2>/dev/null |
        awk 'NF >= 4 { printf "%s\t%s\n", $1, $4 }'
elif command -v rc-status >/dev/null 2>&1; then
    rc-status --all --nocolor 2>/dev/null | awk '/\[/ {gsub(/\[|\]/, ""); printf "%s\t%s\n", $1, $2}'
fi
SERVICES
chmod 0755 "$config_dir/services.sh"
# ZADDESH EVENT LOG INSTALL
printf 'agentAddress udp:%s:%s\nrouser zaddesh priv\nextend zaddesh_services /bin/sh %s/services.sh\n' "$listen_ip" "$agent_port" "$config_dir" > "$config_dir/agent.conf"
chmod 0644 "$config_dir/agent.conf"
printf 'createUser zaddesh SHA "%s" AES "%s"\n' "$auth" "$privacy" > "$state_dir/snmpd.conf"
chown Debian-snmp:Debian-snmp "$state_dir/snmpd.conf"
unset auth privacy passphrase

cat > "$unit" <<'UNIT'
[Unit]
Description=ZadDesh Net-SNMP agent
Wants=network-online.target
After=network-online.target

[Service]
User=Debian-snmp
Group=Debian-snmp
Environment=SNMP_PERSISTENT_DIR=/var/lib/zaddesh-snmp
ExecStart=/usr/sbin/snmpd -f -Lo -C -c /etc/zaddesh-snmp/agent.conf,/var/lib/zaddesh-snmp/snmpd.conf
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=true
UMask=0077
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
chmod 0644 "$unit"
systemctl daemon-reload
systemctl enable --now "$service"
for attempt in {1..20}; do
    if systemctl is-active --quiet "$service" &&
        grep -q '^usmUser ' "$state_dir/snmpd.conf" &&
        ! grep -q '^createUser ' "$state_dir/snmpd.conf"; then
        # Net-SNMP may rotate the original bootstrap file before writing localized keys.
        for backup in "$state_dir"/snmpd.*.conf; do
            if [[ -f $backup ]] && grep -q '^createUser ' "$backup"; then rm -f -- "$backup"; fi
        done
        trap - EXIT
        printf 'SNMPv3 is listening on %s:%s. Allow this UDP port from your collector in the client firewall.\n' "$listen_ip" "$agent_port"
        printf 'Delete the downloaded setup script after provisioning.\n'
        exit 0
    fi
    sleep 0.5
done
fail "SNMPv3 could not start. Check journalctl -u zaddesh-snmp and whether another agent occupies UDP $agent_port."
