# Lets Home Assistant on the Pi (10.0.0.110) see and shut down this PC.
# Run ONCE as administrator:  right-click -> "Mit PowerShell ausführen" (as admin), or from an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\install-ha-remote.ps1
#
# What it does (and nothing else):
#   1. Allows ICMP echo (ping) from the private network, so switch.pc_nutzer in HA shows whether the PC is on.
#   2. Installs and starts Windows' built-in OpenSSH server (port 22, LAN only via the firewall rule it creates).
#   3. Authorizes HA's public key for this account (administrators_authorized_keys, since the account is an admin),
#      so HA can run "shutdown /s /t 30" over SSH. Key-only login; password login over SSH is switched off.
# Undo: Remove-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 ; delete C:\ProgramData\ssh\administrators_authorized_keys ;
#       Disable-NetFirewallRule -DisplayName "Datei- und Druckerfreigabe (Echoanforderung - ICMPv4 eingehend)"

$ErrorActionPreference = "Stop"
$pub = "ssh-ed25519 <oeffentlichen-Schluessel-von-Home-Assistant-hier-eintragen> homeassistant-pc"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Bitte als Administrator ausführen." }

Write-Host "1/3 Ping aus dem Heimnetz erlauben ..."
Get-NetFirewallRule -DisplayName "Datei- und Druckerfreigabe (Echoanforderung - ICMPv4 eingehend)" |
    Where-Object { $_.Profile -match "Private" } | Enable-NetFirewallRule

Write-Host "2/3 OpenSSH-Server installieren und starten ..."
$cap = Get-WindowsCapability -Online -Name "OpenSSH.Server*"
if ($cap.State -ne "Installed") { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
if (-not (Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" -Enabled True `
        -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -Profile Private | Out-Null
}

Write-Host "3/3 Schlüssel von Home Assistant eintragen ..."
$auth = "C:\ProgramData\ssh\administrators_authorized_keys"
if (-not (Test-Path $auth) -or -not (Select-String -Path $auth -SimpleMatch "homeassistant-pc-nutzer" -Quiet)) {
    Add-Content -Path $auth -Value $pub -Encoding ascii
}
# OpenSSH refuses the file unless only SYSTEM and Administrators can read it.
icacls $auth /inheritance:r /grant "SYSTEM:F" /grant "Administratoren:F" | Out-Null

# Key-only: no password guessing over SSH.
$cfg = "C:\ProgramData\ssh\sshd_config"
$text = Get-Content $cfg -Raw
if ($text -notmatch "(?m)^PasswordAuthentication no") {
    $text = $text -replace "(?m)^#?PasswordAuthentication .*$", "PasswordAuthentication no"
    if ($text -notmatch "(?m)^PasswordAuthentication no") { $text += "`r`nPasswordAuthentication no`r`n" }
    Set-Content -Path $cfg -Value $text -Encoding ascii
}
Restart-Service sshd

Write-Host ""
Write-Host "Fertig. sshd läuft: $((Get-Service sshd).Status). Jetzt in Home Assistant 'PC herunterfahren' testen."
