$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
try {
    $events = @(Get-WinEvent -FilterHashtable @{ LogName = @('System', 'Application'); Level = @(1, 2, 3) } -MaxEvents 30 -ErrorAction Stop)
} catch {
    if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { Write-Output 'status|ready' }
    else { Write-Output 'status|unavailable|Event log read access is unavailable' }
    exit
}
Write-Output 'status|ready'
foreach ($event in $events) {
    $at = $event.TimeCreated.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.ffffffZ')
    $identity = 'windows|' + $env:COMPUTERNAME + '|' + $event.LogName + '|' + $event.RecordId + '|' + $at
    $key = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($identity))
    $level = if ($event.Level -eq 1) { 'critical' } elseif ($event.Level -eq 2) { 'error' } else { 'warning' }
    $description = $event.Message
    if (-not $description) { $description = $event.ToXml() }
    $message = $event.ProviderName + ' - Event ' + $event.Id + [Environment]::NewLine + $description
    $encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($message))
    $count = [int][Math]::Ceiling($encoded.Length / 384.0)
    for ($index = 0; $index -lt $count; $index++) {
        $length = [Math]::Min(384, $encoded.Length - $index * 384)
        Write-Output ($key + '|' + $at + '|' + $level + '|' + $index + '|' + $count + '|' + $encoded.Substring($index * 384, $length))
    }
}
