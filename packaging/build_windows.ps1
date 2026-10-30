[CmdletBinding()]
param(
    [switch]$BuildInstaller,
    [switch]$SkipModelDownload,
    [switch]$WithoutModels,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$packageRoot = if ($OutputDirectory) {
    [IO.Path]::GetFullPath((Join-Path $repo $OutputDirectory))
} else {
    Join-Path $repo 'build\package'
}
$stage = Join-Path $repo '.build\models\buffalo_s'
$archive = Join-Path $repo '.build\models\buffalo_s.zip'
$finalInstaller = if ($OutputDirectory) {
    Join-Path $packageRoot 'GlanceSetup.exe'
} else {
    Join-Path $repo 'dist\GlanceSetup.exe'
}
$appVersion = (Get-Content -Raw (Join-Path $repo 'VERSION')).Trim()
if ($appVersion -notmatch '^\d+\.\d+\.\d+$') { throw "Invalid VERSION value: $appVersion" }

function Assert-UnderRepo([string]$Path) {
    $full = [IO.Path]::GetFullPath($Path)
    $prefix = [IO.Path]::GetFullPath($repo) + [IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to modify path outside repository: $full"
    }
}

if ($env:OS -ne 'Windows_NT' -or [Environment]::Is64BitOperatingSystem -ne $true) {
    throw 'The Glance release build requires Windows x64.'
}
if (-not (Test-Path $python)) {
    throw "Python virtual environment missing at $python. Create it and install requirements-lock.txt and requirements-build.txt first."
}
$pythonInfo = & $python -c 'import sys; print(sys.version_info[0], sys.version_info[1], sys.maxsize.bit_length()+1)'
if ($LASTEXITCODE -ne 0 -or $pythonInfo -notmatch '^3\s+12\s+64$') {
    throw "Expected Python 3.12 x64, got $pythonInfo"
}
& $python -m nuitka --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Nuitka is not installed in the build environment.' }

# Only clean dedicated generated paths; never remove models/, data/, or arbitrary dist contents.
foreach ($path in @($packageRoot)) {
    Assert-UnderRepo $path
    if (Test-Path $path) {
        try { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop }
        catch { throw "Could not clean generated build output '$path'. Close Glance or choose a different -OutputDirectory. $($_.Exception.Message)" }
    }
}

if (-not $WithoutModels) {
    if (-not $SkipModelDownload) {
        Assert-UnderRepo $stage
        if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
        & $python (Join-Path $repo 'tools\fetch_models.py') $stage
        if ($LASTEXITCODE -ne 0) { throw 'Pinned model download/validation failed.' }
    } elseif (-not (Test-Path $stage)) {
        throw "Model staging directory is missing: $stage"
    }

    foreach ($model in @('det_500m.onnx', 'w600k_mbf.onnx', 'MODEL.LICENSE')) {
        if (-not (Test-Path (Join-Path $stage $model))) { throw "Required staged asset missing: $model" }
    }
} else {
    Write-Host 'Building without model weights; Glance downloads them on first run.'
}

$modelDataArgs = @()
if (-not $WithoutModels) {
    $modelDataArgs = @(
        "--include-data-files=$(Join-Path $stage 'det_500m.onnx')=models/buffalo_s/det_500m.onnx",
        "--include-data-files=$(Join-Path $stage 'w600k_mbf.onnx')=models/buffalo_s/w600k_mbf.onnx",
        "--include-data-files=$(Join-Path $stage 'MODEL.LICENSE')=models/buffalo_s/MODEL.LICENSE"
    )
}
$noticeDataArgs = @(
    "--include-data-files=$(Join-Path $repo 'packaging\MODEL.LICENSE')=licenses/MODEL.LICENSE"
)

New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
$icon = Join-Path $packageRoot 'Glance.ico'
$iconSource = Join-Path $repo 'assets\GlanceIcon.png'
if (-not (Test-Path $iconSource)) { throw "Approved app artwork is missing: $iconSource" }
& $python (Join-Path $repo 'tools\create_icon.py') $icon '--source' $iconSource
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $icon)) { throw 'Could not create the Windows app icon.' }
$entryPoint = Join-Path $PSScriptRoot 'launch_glance.py'
$args = @(
    '-m', 'nuitka',
    '--standalone',
    '--assume-yes-for-downloads',
    '--lto=no',
    '--mingw64',
    '--enable-plugin=pyside6',
    '--windows-console-mode=disable',
    '--output-filename=Glance',
    "--windows-icon-from-ico=$icon",
    "--company-name=Glance contributors",
    '--product-name=Glance',
    "--file-version=$appVersion",
    "--product-version=$appVersion",
    '--file-description=Glance face unlock settings',
    '--copyright=Copyright 2026 Glance contributors',
    "--output-dir=$packageRoot",
    '--include-module=glance.desktop.main',
    '--include-package=glance',
    "--include-data-files=$iconSource=assets/GlanceIcon.png",
    "--include-data-dir=$(Join-Path $repo 'assets\animations')=assets/animations",
    $entryPoint
)
$args += $modelDataArgs
$args += $noticeDataArgs
& $python @args
if ($LASTEXITCODE -ne 0) { throw 'Nuitka standalone build failed.' }

$agentEntry = Join-Path $PSScriptRoot 'launch_agent.py'
$agentArgs = @(
    '-m', 'nuitka', '--standalone', '--assume-yes-for-downloads', '--lto=no', '--mingw64',
    '--windows-console-mode=disable', '--output-filename=GlanceAgent',
    "--windows-icon-from-ico=$icon",
    "--file-version=$appVersion", "--product-version=$appVersion",
    '--file-description=Glance face verification agent',
    "--output-dir=$packageRoot", '--include-package=glance',
    $agentEntry
)
$agentArgs += $modelDataArgs
$agentArgs += $noticeDataArgs
& $python @agentArgs
if ($LASTEXITCODE -ne 0) { throw 'Standalone agent build failed.' }

$distribution = Join-Path $packageRoot 'launch_glance.dist'
$executable = Join-Path $distribution 'Glance.exe'
if (-not (Test-Path $executable)) { throw "Nuitka output missing: $executable" }
Copy-Item -LiteralPath $icon -Destination (Join-Path $distribution 'Glance.ico') -Force
if (-not (Test-Path (Join-Path $distribution 'assets\GlanceIcon.png'))) {
    throw 'Standalone output is missing the approved app artwork.'
}
if (-not $WithoutModels) {
    foreach ($asset in @('det_500m.onnx', 'w600k_mbf.onnx', 'MODEL.LICENSE')) {
        if (-not (Test-Path (Join-Path $distribution "models\buffalo_s\$asset"))) {
            throw "Standalone output is missing $asset"
        }
    }
} else {
    foreach ($asset in @('det_500m.onnx', 'w600k_mbf.onnx')) {
        if (Test-Path (Join-Path $distribution "models\buffalo_s\$asset")) {
            throw "A model-free build unexpectedly contains $asset"
        }
    }
}
if (-not (Test-Path (Join-Path $distribution 'licenses\MODEL.LICENSE'))) {
    throw 'Standalone output is missing the upstream model notice.'
}
$agentDistribution = Join-Path $packageRoot 'launch_agent.dist'
$agentExecutable = Join-Path $agentDistribution 'GlanceAgent.exe'
if (-not (Test-Path $agentExecutable)) { throw "Standalone agent output missing: $agentExecutable" }
Get-ChildItem -LiteralPath $agentDistribution | Copy-Item -Destination $distribution -Recurse -Force
& (Join-Path $repo 'provider\build.ps1') -Target all
if ($LASTEXITCODE -ne 0) { throw 'Native provider/helper build failed.' }
$providerStage = Join-Path $distribution 'provider'
New-Item -ItemType Directory -Path $providerStage -Force | Out-Null
foreach ($native in @('GlanceProvider.dll', 'grant_tool.exe')) {
    $nativePath = Join-Path $repo "provider\build\$native"
    if (-not (Test-Path $nativePath)) { throw "Native setup component missing: $nativePath" }
    Copy-Item -LiteralPath $nativePath -Destination $providerStage -Force
}
Copy-Item -LiteralPath (Join-Path $repo 'provider\GlanceAdmin.ps1') -Destination $providerStage -Force
Copy-Item -LiteralPath (Join-Path $repo 'provider\GlanceTasks.ps1') -Destination $providerStage -Force
if (-not (Test-Path (Join-Path $distribution 'GlanceAgent.exe'))) {
    throw 'Packaged distribution is missing GlanceAgent.exe.'
}
Copy-Item -LiteralPath (Join-Path $repo 'LICENSE') -Destination $distribution -Force
Copy-Item -LiteralPath (Join-Path $repo 'THIRD_PARTY_NOTICES.md') -Destination $distribution -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'installer-info.txt') -Destination $distribution -Force
$noticeDir = Join-Path $distribution 'THIRD_PARTY'
$packageNames = Get-Content (Join-Path $repo 'requirements-lock.txt') |
    Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') } |
    ForEach-Object { ($_.Trim() -split '==')[0] }
& $python (Join-Path $repo 'tools\collect_notices.py') $noticeDir @packageNames
if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $noticeDir 'INDEX.md'))) {
    throw 'Could not collect third-party dependency notices.'
}

if ($BuildInstaller) {
    $iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
    if (-not $iscc) { throw 'Inno Setup 6 ISCC.exe was not found on PATH.' }
    $temporaryInstaller = Join-Path $packageRoot 'GlanceSetup.tmp.exe'
    & $iscc "/DAppDist=$distribution" "/DOutputDir=$packageRoot" "/DAppVersion=$appVersion" (Join-Path $PSScriptRoot 'inno\Glance.iss')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $temporaryInstaller)) {
        throw 'Inno Setup failed to create a validated installer.'
    }
    $size = (Get-Item $temporaryInstaller).Length
    if ($size -lt 1MB) { throw "Installer output is unexpectedly small ($size bytes)." }
    $distDir = Split-Path $finalInstaller
    New-Item -ItemType Directory -Path $distDir -Force | Out-Null
    Assert-UnderRepo $finalInstaller
    Move-Item -LiteralPath $temporaryInstaller -Destination $finalInstaller -Force
}

Write-Host "Standalone Glance build ready: $executable"
if ($BuildInstaller) { Write-Host "Installer ready: $finalInstaller" }
