# Run in elevated Windows PowerShell. Requires an approved Net-SNMP build
# with SNMPv3 crypto and Windows service support.
# ZADDESH GENERATED PROFILE
$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal ([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this setup from an Administrator terminal.'
}
if ($agentPort -lt 1 -or $agentPort -gt 65535 -or
    $agentAuth -cnotmatch '\A[0-9a-f]{32}\z' -or $agentPrivacy -cnotmatch '\A[0-9a-f]{32}\z') {
    throw 'Download a device-specific setup script from ZadDesh.'
}
$agent = Get-Command snmpd.exe -ErrorAction SilentlyContinue
$agentPath = if ($agent) { $agent.Path } else { Read-Host 'Full path to the approved Net-SNMP snmpd.exe' }
if (-not (Test-Path -LiteralPath $agentPath -PathType Leaf) -or [IO.Path]::GetFileName($agentPath) -ine 'snmpd.exe') {
    throw 'Install a compatible Net-SNMP build before running this setup.'
}
$agentPath = (Resolve-Path -LiteralPath $agentPath).Path
$collector = Read-Host 'Collector IPv4 address (as seen by this client)'
$address = $null
if (-not [Net.IPAddress]::TryParse($collector, [ref]$address) -or $address.ToString() -ne $collector -or
    $address.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
    $address.GetAddressBytes()[0] -eq 0 -or $address.GetAddressBytes()[0] -ge 224) {
    throw 'Enter one unicast collector IPv4 address.'
}
$service = 'Net-SNMP Agent'
$directory = Join-Path $env:ProgramData 'ZadDesh-NetSNMP'
if ((Get-Service -Name $service -ErrorAction SilentlyContinue) -or (Test-Path -LiteralPath $directory)) {
    throw 'An existing Net-SNMP setup was found. Use Custom SNMP settings or remove the existing setup first.'
}
if ([Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveUdpListeners() |
    Where-Object { $_.Port -eq $agentPort }) {
    throw "UDP $agentPort is already in use. Choose a free port in ZadDesh and download a new script."
}

$registrationAttempted = $false
$ruleAdded = $false
$directoryCreated = $false
$ruleName = 'ZadDesh Net-SNMP UDP ' + $agentPort
try {
    New-Item -ItemType Directory -Path $directory | Out-Null
    $directoryCreated = $true
    & icacls.exe $directory /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-19:(OI)(CI)M' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict the Net-SNMP configuration directory.' }

    $config = $directory.Replace('\', '/')
    'Get-Service | Sort-Object Name | ForEach-Object { "{0}`t{1}" -f $_.Name, $_.Status }' |
        Set-Content -LiteralPath (Join-Path $directory 'services.ps1') -Encoding ASCII
# ZADDESH EVENT LOG INSTALL
    $services = 'extend zaddesh_services powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $config + '/services.ps1"'
    @('[snmp]', "persistentDir `"$config`"", '[snmpd]', "agentAddress udp:0.0.0.0:$agentPort", 'rouser zaddesh priv', $services, $eventsExtension) |
        Set-Content -LiteralPath (Join-Path $directory 'agent.conf') -Encoding ASCII
    ('createUser zaddesh SHA "' + $agentAuth + '" AES "' + $agentPrivacy + '"') |
        Set-Content -LiteralPath (Join-Path $directory 'snmpd.conf') -Encoding ASCII

    $registrationAttempted = $true
    & $agentPath -register -C -c "$config/agent.conf,$config/snmpd.conf" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Net-SNMP failed to register its Windows service.' }
    & sc.exe config $service 'obj=' 'NT AUTHORITY\LocalService' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not assign the low-privilege LocalService account.' }
    & netsh.exe advfirewall firewall add rule "name=$ruleName" dir=in action=allow protocol=UDP "localport=$agentPort" "remoteip=$collector" "program=$agentPath" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not configure the scoped Windows Firewall rule.' }
    $ruleAdded = $true
    Set-Service -Name $service -StartupType Automatic
    Start-Service -Name $service
    $state = Join-Path $directory 'snmpd.conf'
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if ((Get-Service -Name $service).Status -eq 'Running' -and
            (Select-String -LiteralPath $state -Pattern '^usmUser ' -Quiet) -and
            -not (Select-String -LiteralPath $state -Pattern '^createUser ' -Quiet)) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) { throw 'Net-SNMP did not initialize the SNMPv3 user. Check the Windows Application event log.' }
    Get-ChildItem -LiteralPath $directory -Filter 'snmpd.*.conf' | ForEach-Object {
        if (Select-String -LiteralPath $_.FullName -Pattern '^createUser ' -Quiet) {
            Remove-Item -LiteralPath $_.FullName -Force
        }
    }
    Write-Host "SNMPv3 is listening on UDP $agentPort. Delete the downloaded setup script after provisioning."
} catch {
    if ($registrationAttempted) {
        Stop-Service -Name $service -ErrorAction SilentlyContinue
        & $agentPath -unregister | Out-Null
    }
    if ($ruleAdded) {
        & netsh.exe advfirewall firewall delete rule "name=$ruleName" "program=$agentPath" | Out-Null
    }
    if ($directoryCreated) {
        Get-ChildItem -LiteralPath $directory -Filter '*.conf' | ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Force
        }
        if (-not (Get-ChildItem -LiteralPath $directory -Force)) {
            Remove-Item -LiteralPath $directory
        }
    }
    throw
}
