# Run in an Administrator PowerShell terminal on a ZadDesh Net-SNMP client.
$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal ([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run from an Administrator terminal.' }
$directory = Join-Path $env:ProgramData 'ZadDesh-NetSNMP'
$configuration = Join-Path $directory 'agent.conf'
if (-not (Test-Path -LiteralPath $configuration -PathType Leaf)) { throw 'Install the ZadDesh Net-SNMP agent first.' }
$service = Get-Service -Name 'Net-SNMP Agent' -ErrorAction Stop
$original = @(Get-Content -LiteralPath $configuration)
$path = $directory.Replace('\', '/')
'Get-Service | Sort-Object Name | ForEach-Object { "{0}`t{1}" -f $_.Name, $_.Status }' |
    Set-Content -LiteralPath (Join-Path $directory 'services.ps1') -Encoding ASCII
# ZADDESH EVENT LOG INSTALL
$updated = @($original | Where-Object { $_ -notmatch '^\s*extend\s+zaddesh_(services|events)\s' })
$updated += 'extend zaddesh_services powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $path + '/services.ps1"'
$updated += $eventsExtension
try {
    $updated | Set-Content -LiteralPath $configuration -Encoding ASCII
    Restart-Service -InputObject $service -ErrorAction Stop
    Write-Host 'Service inventory enabled. Refresh the SNMP snapshot in ZadDesh.'
} catch {
    $original | Set-Content -LiteralPath $configuration -Encoding ASCII
    Restart-Service -InputObject $service -ErrorAction SilentlyContinue
    throw
}
