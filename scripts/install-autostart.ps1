# Jarvis beim Anmelden automatisch starten (Windows-Aufgabenplanung).
#
#   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1            # einrichten
#   powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1 -Remove    # wieder entfernen
#
# Die Aufgabe startet start-jarvis.bat 30 Sekunden nach der Anmeldung (WLAN und
# Home Assistant sind dann erreichbar), nur für diesen Benutzer, ohne Adminrechte.
# Stürzt Jarvis ab, startet ihn server.py selbst neu; endet der ganze Prozess
# trotzdem, versucht die Aufgabe es bis zu dreimal im Minutenabstand erneut.
param([switch]$Remove)

$name = "Jarvis HUD"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Autostart entfernt."
    return
}

$root = Split-Path -Parent $PSScriptRoot
$bat = Join-Path $root "start-jarvis.bat"
if (-not (Test-Path $bat)) { throw "start-jarvis.bat nicht gefunden: $bat" }

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$trigger.Delay = "PT30S"
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Startet den Jarvis-Sprachassistenten beim Anmelden." -Force | Out-Null
Write-Host "Autostart eingerichtet: '$name' startet $bat bei der Anmeldung."
