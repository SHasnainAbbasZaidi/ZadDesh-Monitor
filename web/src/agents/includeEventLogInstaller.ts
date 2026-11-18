import windowsEvents from './events-windows.ps1?raw';
import linuxEvents from './events-linux.py?raw';

/** Embed the versioned event reader in downloaded client installers. */
export function includeEventLogInstaller(script: string, platform: 'windows' | 'linux') {
  const windows = [
    "@'", windowsEvents.trim(),
    "'@ | Set-Content -LiteralPath (Join-Path $directory 'events.ps1') -Encoding UTF8",
    "$eventsExtension = 'extend zaddesh_events powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"' + $directory.Replace('\\', '/') + '/events.ps1\"'",
    "$readers = (New-Object Security.Principal.SecurityIdentifier('S-1-5-32-573')).Translate([Security.Principal.NTAccount]).Value.Split('\\')[-1]",
    "$localService = (New-Object Security.Principal.SecurityIdentifier('S-1-5-19')).Translate([Security.Principal.NTAccount]).Value",
    "$members = & net.exe localgroup $readers 2>&1",
    "if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Event Log Readers membership.' }",
    "if (-not ($members | Where-Object { $_.Trim() -ieq $localService })) {",
    "    & net.exe localgroup $readers $localService /add | Out-Null",
    "    if ($LASTEXITCODE -ne 0) { throw 'Could not grant event-log read access to LocalService.' }",
    "}",
  ].join('\n');
  const linux = [
    'command -v python3 >/dev/null || { apt-get update && apt-get install -y python3; }',
    'if getent group systemd-journal >/dev/null; then usermod -a -G systemd-journal Debian-snmp; fi',
    "cat > /etc/zaddesh-snmp/events.py <<'ZADDESH_EVENT_READER'", linuxEvents.trim(), 'ZADDESH_EVENT_READER',
    'chmod 0755 /etc/zaddesh-snmp/events.py',
  ].join('\n');
  let result = script.replace('# ZADDESH EVENT LOG INSTALL', platform === 'windows' ? windows : linux);
  if (platform === 'linux') {
    const extension = "printf 'extend zaddesh_events /usr/bin/python3 /etc/zaddesh-snmp/events.py\\n' >> \"$configuration\"";
    result = script.includes('# ZADDESH GENERATED PROFILE')
      ? result.replace('chmod 0644 "$config_dir/agent.conf"', extension.replace('$configuration', '$config_dir/agent.conf') + '\nchmod 0644 "$config_dir/agent.conf"')
      : result.replace('systemctl restart zaddesh-snmp.service\ntrap - ERR', "sed -i '/^[[:space:]]*extend[[:space:]]\\+zaddesh_events[[:space:]]/d' \"$configuration\"\n" + extension + '\nsystemctl restart zaddesh-snmp.service\ntrap - ERR');
  }
  return result;
}
