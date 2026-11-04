# Start Jarvis automatically at sign-in (Windows Task Scheduler).
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1            # set up
#   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1 -Remove    # remove again
#
# The task starts start-jarvis.bat 30 seconds after sign-in (WiFi and
# Home Assistant can be reached by then), only for this user, without admin rights.
# If Jarvis crashes, server.py restarts it itself; should the whole process end
# anyway, the task tries again up to three times, one minute apart.
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
Write-Host "Autostart set up: '$name' starts $bat at sign-in."
