param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$MonitorArgs
)

if (-not $MonitorArgs -or $MonitorArgs.Count -eq 0) {
    $MonitorArgs = @('help')
}

$sshArgs = @('jarvis', 'sudo', '/usr/local/bin/monitor-admin') + $MonitorArgs
& ssh @sshArgs
exit $LASTEXITCODE
