# Run with elevated PowerShell. Only allows trusted Private network + local subnet.
$ErrorActionPreference = "Stop"
$rule = "JableTVDownload LAN TCP 6003"
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Please run this script as Administrator."; exit 1
}
Get-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $rule -Enabled True -Direction Inbound -Action Allow -Protocol TCP -LocalPort 6003 -Profile Private -RemoteAddress LocalSubnet | Out-Null
Write-Host "Firewall rule applied: TCP 6003, Private network, LocalSubnet only."
Write-Host "Current network profile(s):"
Get-NetConnectionProfile | Format-Table InterfaceAlias,NetworkCategory
Write-Host "LAN IPv4 address(es):"
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } | Select-Object InterfaceAlias,IPAddress | Format-Table
Write-Host "Visit http://<server-ip>:6003 from another device in the same subnet."
