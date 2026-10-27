[CmdletBinding()]
param(
    [Parameter()]
    [ValidateSet('install-provider', 'disable-provider', 'reset-glance', 'status')]
    [string]$Operation,

    [Parameter(Mandatory)]
    [string]$TargetSid
)

$ErrorActionPreference = 'Stop'
$guid = '{8F3A2C41-7B6D-4E59-9A21-5C0D7E3B1F84}'
$registryBase = 'SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication'
$providerReg = "$registryBase\Credential Providers\$guid"
$filterReg = "$registryBase\Credential Provider Filters\$guid"
$clsidReg = "SOFTWARE\Classes\CLSID\$guid"
$glanceReg = 'SOFTWARE\Glance'
$machineData = Join-Path $env:ProgramData 'Glance'

function Write-Result([string]$Status, [string]$Message) {
    $record = @{ operation = $Operation; status = $Status; message = $Message } |
        ConvertTo-Json -Compress
    Write-Output "GLANCE_ADMIN_RESULT=$record"
}

function Get-UserProfile([string]$Sid) {
    $key = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\$Sid"
    if (-not (Test-Path $key)) { throw "No Windows profile exists for the requested account SID." }
    $profile = [Environment]::ExpandEnvironmentVariables((Get-ItemProperty -LiteralPath $key).ProfileImagePath)
    if (-not (Test-Path -LiteralPath $profile -PathType Container)) { throw "The target user's profile is unavailable." }
    return [IO.Path]::GetFullPath($profile)
}

function Get-TaskService {
    $service = New-Object -ComObject Schedule.Service
    $service.Connect()
    return $service
}

function Stop-AndRemoveAgentTask {
    $service = Get-TaskService
    $folder = $service.GetFolder('\')
    try {
        $task = $folder.GetTask('Glance Agent')
        try { $task.Stop(0) } catch { }
        $folder.DeleteTask('Glance Agent', 0)
    } catch {
        # Missing task is already disabled.
    }
}

function Remove-ProviderRegistration {
    foreach ($path in @("HKLM:\$providerReg", "HKLM:\$filterReg", "HKLM:\$clsidReg")) {
        if (Test-Path -LiteralPath $path) {
            Remove-Item -LiteralPath $path -Recurse -Force
        }
    }
}

function Disable-Provider([bool]$ClearCredential) {
    # Deregister first: LogonUI must stop discovering the provider before file cleanup.
    Remove-ProviderRegistration
    Stop-AndRemoveAgentTask
    Remove-Item -LiteralPath (Join-Path $machineData 'grant.bin') -Force -ErrorAction SilentlyContinue

    $grantTool = Join-Path $PSScriptRoot 'grant_tool.exe'
    if ($ClearCredential -and (Test-Path -LiteralPath $grantTool)) {
        & $grantTool clear | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not clear the stored Windows credential.' }
        Remove-Item -LiteralPath "HKLM:\$glanceReg" -Recurse -Force -ErrorAction SilentlyContinue
    }

    $dll = Join-Path $env:WINDIR 'System32\GlanceProvider.dll'
    if (Test-Path -LiteralPath $dll) {
        try {
            Remove-Item -LiteralPath $dll -Force -ErrorAction Stop
        } catch {
            $moveFileEx = Add-Type -Namespace GlanceNative -Name Files -PassThru -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode, SetLastError = true)]
public static extern bool MoveFileEx(string existing, string replacement, int flags);
'@
            # MOVEFILE_DELAY_UNTIL_REBOOT: a loaded LogonUI module cannot be deleted now.
            if (-not $moveFileEx::MoveFileEx($dll, $null, 4)) {
                throw "Could not remove or schedule removal of the provider DLL."
            }
        }
    }
}

try {
    if ($Operation -eq 'install-provider') {
        if ($TargetSid -notmatch '^S-1-5-21-(\d+-){2,}\d+$') { throw 'The requested account SID is invalid.' }
        $sidObject = [System.Security.Principal.SecurityIdentifier]::new($TargetSid)
    }

    $installRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $programFiles = [IO.Path]::GetFullPath($env:ProgramFiles).TrimEnd('\')
    $expectedRoot = [IO.Path]::GetFullPath((Join-Path $programFiles 'Glance'))
    if (-not $installRoot.Equals($expectedRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Glance administrative files are not running from the protected Program Files installation.'
    }

    $providerSource = Join-Path $PSScriptRoot 'GlanceProvider.dll'
    $grantTool = Join-Path $PSScriptRoot 'grant_tool.exe'
    $agent = Join-Path $installRoot 'GlanceAgent.exe'
    switch ($Operation) {
        'install-provider' {
            $profile = Get-UserProfile $TargetSid
            $userData = Join-Path $profile 'AppData\Local\Glance\data'
            foreach ($required in @(
                $providerSource, $grantTool, $agent,
                (Join-Path $installRoot 'models\buffalo_s\det_500m.onnx'),
                (Join-Path $installRoot 'models\buffalo_s\w600k_mbf.onnx'),
                (Join-Path $userData 'agent_config.json'),
                (Join-Path $userData 'identities.json')
            )) {
                if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required setup file is missing: $required" }
            }

            $config = Get-Content -LiteralPath (Join-Path $userData 'agent_config.json') -Raw | ConvertFrom-Json
            $identity = [string]$config.identity
            if ([string]::IsNullOrWhiteSpace($identity)) { throw 'Select an enrolled face before enabling Windows unlock.' }
            $identityStore = Get-Content -LiteralPath (Join-Path $userData 'identities.json') -Raw | ConvertFrom-Json
            if (-not ($identityStore.identities | Where-Object { $_.name -ceq $identity })) {
                throw 'The selected face is not present in the enrollment store.'
            }

            $status = & $grantTool status 2>&1 | Out-String
            if ($LASTEXITCODE -ne 0 -or $status -notmatch 'stored credential:\s*present') {
                throw 'Verify and save the Windows password in Glance before enabling face unlock.'
            }

            if ($status -notmatch 'grant key\s*:\s*configured') {
                & $grantTool init | Out-Null
                if ($LASTEXITCODE -ne 0) { throw 'Could not initialize Glance machine security state.' }
            }

            New-Item -ItemType Directory -Path $machineData -Force | Out-Null
            $acl = Get-Acl -LiteralPath $machineData
            $acl.SetAccessRuleProtection($true, $false)
            $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                'NT AUTHORITY\SYSTEM', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
            $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                'BUILTIN\Administrators', 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
            $acl.AddAccessRule([System.Security.AccessControl.FileSystemAccessRule]::new(
                $sidObject, 'Modify', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
            Set-Acl -LiteralPath $machineData -AclObject $acl

            $targetDll = Join-Path $env:WINDIR 'System32\GlanceProvider.dll'
            Copy-Item -LiteralPath $providerSource -Destination $targetDll -Force
            $base = [Microsoft.Win32.Registry]::LocalMachine
            foreach ($pair in @(
                @($providerReg, 'Glance face unlock'),
                @($filterReg, 'Glance face unlock'),
                @($clsidReg, 'Glance face unlock')
            )) {
                $key = $base.CreateSubKey([string]$pair[0])
                try { $key.SetValue('', [string]$pair[1]) } finally { $key.Dispose() }
            }
            $inproc = $base.CreateSubKey("$clsidReg\InprocServer32")
            try {
                $inproc.SetValue('', $targetDll)
                $inproc.SetValue('ThreadingModel', 'Apartment')
            } finally { $inproc.Dispose() }

            $service = Get-TaskService
            $folder = $service.GetFolder('\')
            $task = $service.NewTask(0)
            $task.RegistrationInfo.Description = 'Glance face verification for this Windows user'
            $task.Settings.Enabled = $true
            $task.Settings.StartWhenAvailable = $true
            $task.Settings.DisallowStartIfOnBatteries = $false
            $task.Principal.UserId = $TargetSid
            $task.Principal.LogonType = 3 # TASK_LOGON_INTERACTIVE_TOKEN
            $task.Principal.RunLevel = 0 # TASK_RUNLEVEL_LUA
            $action = $task.Actions.Create(0)
            $action.Path = $agent
            $trigger = $task.Triggers.Create(9) # TASK_TRIGGER_LOGON
            $trigger.UserId = $TargetSid
            $folder.RegisterTaskDefinition('Glance Agent', $task, 6, $TargetSid, $null, 3, $null) | Out-Null
            Write-Result 'ok' 'Provider registered and user agent task installed.'
        }
        'disable-provider' {
            Disable-Provider $true
            Write-Result 'ok' 'Face unlock disabled; provider, agent task, saved credential, and pending grant removed.'
        }
        'reset-glance' {
            Disable-Provider $true
            Remove-Item -LiteralPath $machineData -Recurse -Force -ErrorAction SilentlyContinue
            Write-Result 'ok' 'Glance machine state reset.'
        }
        'status' {
            $registered = Test-Path -LiteralPath "HKLM:\$providerReg"
            $task = $false
            try { $null = (Get-TaskService).GetFolder('\').GetTask('Glance Agent'); $task = $true } catch { }
            $credential = $false
            if (Test-Path -LiteralPath $grantTool) {
                $status = & $grantTool status 2>&1 | Out-String
                $credential = $LASTEXITCODE -eq 0 -and $status -match 'stored credential:\s*present'
            }
            Write-Result 'ok' (@{ provider = $registered; task = $task; credential = $credential } | ConvertTo-Json -Compress)
        }
    }
    exit 0
} catch {
    if ($Operation -eq 'install-provider') {
        try { Disable-Provider $false } catch { }
    }
    Write-Result 'error' $_.Exception.Message
    exit 1
}
