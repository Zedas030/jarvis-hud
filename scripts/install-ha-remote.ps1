# Lets Home Assistant on the Pi (10.0.0.110) see and shut down this PC.
# Run ONCE as administrator:  right-click -> "Run with PowerShell" (as admin), or from an elevated PowerShell:
#   powershell -ExecutionPolicy Bypass -File scripts\install-ha-remote.ps1
#
# What it does (and nothing else):
#   1. Allows ICMP echo (ping) from the private network, so switch.pc_user in HA shows whether the PC is on.
#   2. Installs and starts Windows' built-in OpenSSH server (port 22, LAN only via the firewall rule it creates).
#   3. Authorizes HA's public key for this account (administrators_authorized_keys, since the account is an admin),
#      so HA can run "shutdown /s /t 30" over SSH. Key-only login; password login over SSH is switched off.
# Undo: Remove-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 ; delete C:\ProgramData\ssh\administrators_authorized_keys ;
#       Disable-NetFirewallRule -Name "FPS-ICMP4-ERQ-In"

$ErrorActionPreference = "Stop"
$pub = "ssh-ed25519 <enter-the-public-key-of-home-assistant-here> homeassistant-pc"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "Please run as administrator." }

Write-Host "1/3 Allow ping from the home network ..."
# by the rule's internal name: the display name depends on the Windows language
Get-NetFirewallRule -Name "FPS-ICMP4-ERQ-In*" |
    Where-Object { $_.Profile -match "Private" } | Enable-NetFirewallRule

Write-Host "2/3 Install and start the OpenSSH server ..."
$cap = Get-WindowsCapability -Online -Name "OpenSSH.Server*"
if ($cap.State -ne "Installed") { Add-WindowsCapability -Online -Name $cap.Name | Out-Null }
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
if (-not (Get-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -Name "OpenSSH-Server-In-TCP" -DisplayName "OpenSSH Server (sshd)" -Enabled True `
        -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -Profile Private | Out-Null
}

Write-Host "3/3 Enter the key of Home Assistant ..."
$auth = "C:\ProgramData\ssh\administrators_authorized_keys"
if (-not (Test-Path $auth) -or -not (Select-String -Path $auth -SimpleMatch "homeassistant-pc" -Quiet)) {
    Add-Content -Path $auth -Value $pub -Encoding ascii
}
# OpenSSH refuses the file unless only SYSTEM and Administrators can read it.
# by SID: the group name depends on the Windows language (SYSTEM, Administrators)
icacls $auth /inheritance:r /grant "*S-1-5-18:F" /grant "*S-1-5-32-544:F" | Out-Null

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
Write-Host "Done. sshd is running: $((Get-Service sshd).Status). Now test 'shut down PC' in Home Assistant."
