# Start Jarvis automatically at sign-in (Windows Task Scheduler).
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1            # install
#   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1 -Remove    # remove
#
# The task runs start-jarvis.bat 30 seconds after sign-in (Wi-Fi and Home
# Assistant are reachable by then), for this user only, without admin rights.
# If Jarvis crashes, server.py restarts it itself; if the whole process still
# ends, the task retries up to three times, one minute apart.
param([switch]$Remove)

$name = "Jarvis HUD"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Autostart removed."
    return
}

$root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $root "start-jarvis.bat"
if (-not (Test-Path $bat)) { throw "start-jarvis.bat not found: $bat" }

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$trigger.Delay = "PT30S"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Starts the Jarvis voice assistant at sign-in." -Force | Out-Null
Write-Host "Autostart installed: '$name' runs $bat at sign-in."
