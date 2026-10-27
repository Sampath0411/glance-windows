#Requires -RunAsAdministrator
<#
    Removes Glance's credential provider.

    Safe to run at any point, including after a half-finished install: every step
    tolerates a missing piece. The registry entries are removed first on purpose -
    once they are gone, LogonUI no longer loads the DLL even if the file is still
    on disk.

    .\uninstall.ps1                keep the stored password and the grant key
    .\uninstall.ps1 -Purge         also delete the credential, key and data
#>

[CmdletBinding()]
param(
    [switch]$Purge
)

$ErrorActionPreference = 'Continue'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $here
$tool = Join-Path $here 'build\grant_tool.exe'
$target = Join-Path $env:WINDIR 'System32\GlanceProvider.dll'
$dataDir = 'C:\ProgramData\Glance'

$providerKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}'
$filterKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Provider Filters\{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}'
$clsidKey = 'HKLM:\SOFTWARE\Classes\CLSID\{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}'

Write-Host 'Glance for Windows - uninstall' -ForegroundColor White

# 1. deregister first: this is what stops LogonUI loading the DLL.
Write-Host "`n== removing registration" -ForegroundColor Cyan
foreach ($key in @($providerKey, $filterKey, $clsidKey)) {
    if (Test-Path $key) {
        Remove-Item $key -Recurse -Force
        Write-Host "   removed $key" -ForegroundColor Green
    } else {
        Write-Host "   already absent: $key"
    }
}

# 2. stop the agent if it is running.
Write-Host "`n== stopping the agent" -ForegroundColor Cyan
Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" |
    Where-Object { $_.CommandLine -like '*glance_agent.py*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "   stopped pid $($_.ProcessId)" -ForegroundColor Green
    }

Write-Host "`n== removing the logon task" -ForegroundColor Cyan
schtasks /delete /tn 'Glance Agent' /f 2>$null | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host '   removed task Glance Agent' -ForegroundColor Green }
else { Write-Host '   no logon task present' }

# 3. the DLL. If LogonUI holds it, schedule removal at next boot instead.
Write-Host "`n== removing the DLL" -ForegroundColor Cyan
if (Test-Path $target) {
    try {
        Remove-Item $target -Force -ErrorAction Stop
        Write-Host "   deleted $target" -ForegroundColor Green
    } catch {
        Add-Type -Namespace Native -Name File -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode, SetLastError = true)]
public static extern bool MoveFileEx(string existing, string replacement, int flags);
'@
        # MOVEFILE_DELAY_UNTIL_REBOOT
        $null = [Native.File]::MoveFileEx($target, $null, 4)
        Write-Host '   in use - scheduled for deletion at next reboot' -ForegroundColor Yellow
    }
} else {
    Write-Host '   already absent'
}

# 4. optionally remove secrets.
if ($Purge) {
    Write-Host "`n== purging secrets" -ForegroundColor Cyan
    if (Test-Path $tool) {
        & $tool clear | ForEach-Object { "   $_" }
    }
    Remove-Item 'HKLM:\SOFTWARE\Glance' -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $dataDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host '   stored password, grant key and data directory removed' -ForegroundColor Green
} else {
    Write-Host "`n   stored password and grant key kept (use -Purge to remove)" -ForegroundColor Yellow
}

Write-Host ''
Write-Host 'Uninstalled. Sign out and back in to confirm the normal login screen.' -ForegroundColor Green
Write-Host 'If a reboot is pending for the DLL, do that before trusting the result.' -ForegroundColor Yellow
Write-Host ''
