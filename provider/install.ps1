#Requires -RunAsAdministrator
<#
    Installs Glance's credential provider.

    Run this only after harness\\provider_harness.exe passes. The ordering matters:
    the DLL is copied and registered last, so a failure in enrolment leaves the
    machine exactly as it was.

    .\install.ps1                 full install, prompts for your Windows password
    .\install.ps1 -SkipEnroll     register only (enrol later)
    .\install.ps1 -NoAutostart    do not create the logon task

    Rollback is .\uninstall.ps1, and it works even if this script half-finished.
#>

[CmdletBinding()]
param(
    [string]$Identity,
    [switch]$SkipEnroll,
    [switch]$NoAutostart
)

$ErrorActionPreference = 'Stop'

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here 'GlanceTasks.ps1')
$repo = Split-Path -Parent $here
$dll = Join-Path $here 'build\GlanceProvider.dll'
$tool = Join-Path $here 'build\grant_tool.exe'
$system32 = Join-Path $env:WINDIR 'System32'
$dataDir = 'C:\ProgramData\Glance'

$providerKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Providers\{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}'
$filterKey = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\Credential Provider Filters\{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}'
$clsidKey = 'HKLM:\SOFTWARE\Classes\CLSID\{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}'
$inprocKey = "$clsidKey\InprocServer32"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Good($text) { Write-Host "   $text" -ForegroundColor Green }
function Warn($text) { Write-Host "   $text" -ForegroundColor Yellow }

Write-Host "Glance for Windows - credential provider install" -ForegroundColor White
Write-Host "Nothing here changes how you log in until the DLL is registered at the end."

# --- 0. sanity -------------------------------------------------------------
Step 'checking the build'
if (-not (Test-Path $dll)) { throw "missing $dll - run provider\build.ps1 -Target all first" }
if (-not (Test-Path $tool)) { throw "missing $tool - run provider\build.ps1 -Target all first" }

$principal = [Security.Principal.WindowsIdentity]::GetCurrent()
Good "dll : $dll"
Good "tool: $tool"
Good "running as: $($principal.Name)"

# A quick reminder that the harness has to have passed; this is not enforced
# mechanically because a fresh build legitimately has no result recorded yet.
if (Test-Path (Join-Path $here 'build\provider_harness.exe')) {
    Warn 'harness exists - run it and confirm 36/36 before continuing'
}

# --- 1. data directory and grant key --------------------------------------
Step 'preparing the data directory and grant key'
& $tool init | ForEach-Object { "   $_" }
if ($LASTEXITCODE -ne 0) { throw 'grant_tool init failed' }

New-Item -ItemType Directory -Force -Path $dataDir | Out-Null

# The agent runs as the signed-in user and must be able to write grant.bin and
# agent.log. Restricting the directory to SYSTEM alone would break the agent; the
# grant key, not the directory, is what protects the file.
$acl = Get-Acl $dataDir
$acl.SetAccessRuleProtection($true, $false)

$systemRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    'NT AUTHORITY\SYSTEM', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
$adminRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    'BUILTIN\Administrators', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
$userRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    'BUILTIN\Users', 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow')

$acl.AddAccessRule($systemRule)
$acl.AddAccessRule($adminRule)
$acl.AddAccessRule($userRule)
Set-Acl -Path $dataDir -AclObject $acl
Good "data directory: $dataDir (Users: Modify, SYSTEM/Admins: Full)"

Warn 'grant key: readable by users by design (the agent signs grants as the user)'
Warn 'this does not defend against code already running as you - see PHASE3.md'

# --- 2. stored credential --------------------------------------------------
Step 'storing the Windows password for the lock screen'
if ($SkipEnroll) {
    Warn 'skipped (-SkipEnroll); face unlock will refuse until you enrol'
} else {
    $account = "$env:USERDOMAIN\$env:USERNAME"
    Warn "account: $account"
    Warn 'you will be asked for your Windows password; it is kept DPAPI-encrypted'
    & $tool enroll --account $account
    if ($LASTEXITCODE -ne 0) { throw 'enrolment failed - nothing has been registered' }
}

# --- 3. which identity may unlock -----------------------------------------
Step 'choosing which identity may unlock this account'
$storePath = Join-Path $repo 'data\identities.json'
if (-not (Test-Path $storePath)) {
    throw "no enrolment at $storePath - enrol a face first (scripts\run.bat option 1)"
}
$names = (Get-Content $storePath -Raw | ConvertFrom-Json).identities |
    ForEach-Object { $_.name }

if ($Identity) {
    if ($names -notcontains $Identity) {
        throw "identity '$Identity' is not enrolled (enrolled: $($names -join ', '))"
    }
} elseif ($names.Count -eq 1) {
    $Identity = $names[0]
} else {
    Write-Host "   enrolled identities: $($names -join ', ')" -ForegroundColor Yellow
    $Identity = Read-Host '   which one may unlock this account?'
    if ($names -notcontains $Identity) { throw "not an enrolled identity: $Identity" }
}
Good "unlock permitted for: $Identity only"

# --- 4. copy the DLL -------------------------------------------------------
Step 'installing the provider DLL'
$target = Join-Path $system32 'GlanceProvider.dll'
try {
    Copy-Item $dll $target -Force
    Good "copied to $target"
} catch {
    throw "could not copy to System32 (LogonUI may have it loaded): $($_.Exception.Message)"
}

# --- 5. register -----------------------------------------------------------
Step 'registering the credential provider'
New-Item -Path $providerKey -Force | Out-Null
Set-ItemProperty -Path $providerKey -Name '(default)' -Value 'Glance face unlock'
New-Item -Path $filterKey -Force | Out-Null
Set-ItemProperty -Path $filterKey -Name '(default)' -Value 'Glance face unlock'

# LogonUI does not read the DLL path from the provider key; it resolves the CLSID as a
# COM object. Both keys are required, and omitting the second fails silently.
New-Item -Path $inprocKey -Force | Out-Null
Set-ItemProperty -Path $clsidKey -Name '(default)' -Value 'Glance face unlock'
Set-ItemProperty -Path $inprocKey -Name '(default)' -Value $target
Set-ItemProperty -Path $inprocKey -Name 'ThreadingModel' -Value 'Apartment'
Good 'registered: provider entry, filter entry, CLSID + InprocServer32'

# --- 6. autostart ----------------------------------------------------------
if (-not $NoAutostart) {
    Step 'registering the agent to start at logon'
    $pythonw = Join-Path $repo '.venv\Scripts\pythonw.exe'
    $agent = Join-Path $repo 'scripts\launch_agent.py'
    if (-not (Test-Path $pythonw)) {
        Warn "pythonw not found at $pythonw - skipping autostart"
    } else {
        # The agent reads its runtime settings from this file, so the desktop app can
        # change the identity, threshold or timeout without rewriting the task.
        $configPath = Join-Path $repo 'data\agent_config.json'
        $existing = $null
        if (Test-Path $configPath) {
            try { $existing = Get-Content $configPath -Raw | ConvertFrom-Json } catch { $existing = $null }
        }
        $threshold = if ($existing -and $existing.threshold) { $existing.threshold } else { 0.60 }
        $timeout = if ($existing -and $existing.timeout) { $existing.timeout } else { 90 }
        $camera = if ($existing -and $null -ne $existing.camera) { $existing.camera } else { 0 }
        $config = [ordered]@{
            threshold = $threshold
            timeout = $timeout
            identity = $Identity
            camera = $camera
        }
        New-Item -ItemType Directory -Path (Split-Path $configPath) -Force | Out-Null
        $config | ConvertTo-Json | Set-Content -Path $configPath -Encoding UTF8
        Good "agent settings written to data\agent_config.json (identity $Identity)"

        $action = "`"$pythonw`" `"$agent`""
        schtasks /create /tn 'Glance Agent' /tr $action /sc onlogon /rl limited /f | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Good "logon task 'Glance Agent' created"
            Register-GlanceResumeTask ([System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value) $pythonw "`"$agent`""
            Good "wake task 'Glance Agent Resume' created"
        }
        else { Warn 'could not create the logon task; start the agent by hand' }
    }
} else {
    Warn 'autostart skipped (-NoAutostart)'
}

# --- 7. report -------------------------------------------------------------
Step 'state'
& $tool status | ForEach-Object { "   $_" }

Write-Host ''
Write-Host 'Installed.' -ForegroundColor Green
Write-Host ''
Write-Host 'Try it:' -ForegroundColor White
Write-Host '  1. start the agent if it is not running:'
Write-Host "     $repo\.venv\Scripts\python.exe $repo\scripts\launch_agent.py --identity $Identity"
Write-Host '  2. press Win+L, look at the camera'
Write-Host ''
Write-Host 'If the Face tile never appears, or login misbehaves:' -ForegroundColor Yellow
Write-Host '  * your password still works - the provider fails closed to it'
Write-Host '  * run uninstall.ps1 from an elevated prompt'
Write-Host '  * if you cannot log in at all, see RECOVERY.txt (WinRE, no login needed)'
Write-Host ''
