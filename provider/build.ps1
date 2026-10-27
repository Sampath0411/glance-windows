# Builds the Phase 3 C++ pieces with MSVC.
#
#   .\build.ps1              # grant_tool.exe (harness, no COM)
#   .\build.ps1 -Target provider   # GlanceProvider.dll (needs COM headers)
#   .\build.ps1 -Target all
#
# Deliberately does NOT install or register anything. Building and installing are
# separate steps on purpose: an unbuilt DLL cannot lock anyone out.

param(
    [ValidateSet('tools', 'provider', 'harness', 'all')]
    [string]$Target = 'tools'
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    throw "vswhere not found. Install Visual Studio Build Tools with the C++ workload."
}

$vs = & $vswhere -latest -products * `
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    -property installationPath
if (-not $vs) {
    throw "C++ toolset not found. The Build Tools install may still be running."
}

$vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { throw "vcvars64.bat missing at $vcvars" }

$out = Join-Path $here 'build'
New-Item -ItemType Directory -Force -Path $out | Out-Null

$common = '/nologo /std:c++17 /EHsc /W4 /O2 /DUNICODE /D_UNICODE'
$bat = Join-Path $env:TEMP 'glance_build.bat'
$lines = @(
    '@echo off',
    "call `"$vcvars`" >nul",
    "cd /d `"$here`""
)

if ($Target -in @('tools', 'all')) {
    $lines += "cl $common /Fo`"$out\\`" /Fe`"$out\grant_tool.exe`" GlanceGrant.cpp GlanceSecret.cpp GlancePack.cpp grant_tool.cpp bcrypt.lib wtsapi32.lib advapi32.lib crypt32.lib secur32.lib ole32.lib"
    $lines += 'if errorlevel 1 exit /b 1'
}

if ($Target -in @('provider', 'all')) {
    $lines += "cl $common /LD /Fo`"$out\\`" /Fe`"$out\GlanceProvider.dll`" GlanceGrant.cpp GlancePack.cpp GlanceProvider.cpp GlanceSecret.cpp ole32.lib oleaut32.lib uuid.lib advapi32.lib bcrypt.lib wtsapi32.lib crypt32.lib /link /DEF:GlanceProvider.def /SUBSYSTEM:WINDOWS"
    $lines += 'if errorlevel 1 exit /b 1'
}

if ($Target -in @('harness', 'all')) {
    $lines += "cl $common /Fo`"$out\\`" /Fe`"$out\provider_harness.exe`" GlanceGrant.cpp GlancePack.cpp GlanceSecret.cpp provider_harness.cpp ole32.lib oleaut32.lib uuid.lib advapi32.lib bcrypt.lib wtsapi32.lib crypt32.lib"
    $lines += 'if errorlevel 1 exit /b 1'
}

Set-Content -Path $bat -Value ($lines -join "`r`n") -Encoding ASCII
& cmd /c $bat
$code = $LASTEXITCODE
Remove-Item $bat -ErrorAction SilentlyContinue

if ($code -ne 0) { throw "build failed with exit code $code" }

Write-Host ""
Write-Host "built into $out" -ForegroundColor Green
Get-ChildItem $out -Filter *.exe | Select-Object Name, Length | Format-Table -AutoSize
Get-ChildItem $out -Filter *.dll | Select-Object Name, Length | Format-Table -AutoSize
