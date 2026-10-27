# Phase 3 prerequisites - must run elevated (one UAC prompt).
#
# 1. Enables the built-in Administrator account and lets YOU type its password.
#    Windows prompts twice; this script never sees or stores what you type.
# 2. Installs Visual Studio Build Tools with the C++ workload and Windows SDK.
#
# Why 1 first: if the credential provider ever misbehaves, this is the account that
# still gets you an admin desktop.

$ErrorActionPreference = 'Continue'

Write-Host ""
Write-Host "=== 1 of 2: recovery account ===" -ForegroundColor Cyan
net user Administrator /active:yes
Write-Host ""
Write-Host "Now set a password for the Administrator account." -ForegroundColor Yellow
Write-Host "This is your way back in if the credential provider breaks login." -ForegroundColor Yellow
Write-Host "Windows will ask you to type it twice." -ForegroundColor Yellow
Write-Host ""
net user Administrator *

Write-Host ""
Write-Host "Recovery account state:" -ForegroundColor Cyan
Get-LocalUser -Name Administrator | Format-List Name, Enabled, PasswordRequired, LastLogon

Write-Host ""
Write-Host "=== 2 of 2: Visual Studio Build Tools + Windows SDK ===" -ForegroundColor Cyan
Write-Host "This downloads roughly 3-4 GB and takes 10-20 minutes." -ForegroundColor Yellow
Write-Host "Leave this window open. It will say 'done' when finished." -ForegroundColor Yellow
Write-Host ""

winget install --id Microsoft.VisualStudio.2022.BuildTools `
    --accept-source-agreements `
    --accept-package-agreements `
    --override "--wait --quiet --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"

Write-Host ""
Write-Host "winget exit code: $LASTEXITCODE" -ForegroundColor Cyan
Write-Host ""
Write-Host "Checking for the compiler and SDK header..." -ForegroundColor Cyan
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vswhere) {
    $path = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    Write-Host "VS install: $path"
}
$hdr = Get-ChildItem "C:\Program Files (x86)\Windows Kits\10\Include" -Recurse -Filter credentialprovider.h -ErrorAction SilentlyContinue | Select-Object -First 1
if ($hdr) { Write-Host "credentialprovider.h: $($hdr.FullName)" -ForegroundColor Green }
else { Write-Host "credentialprovider.h: still missing - SDK component may not have installed" -ForegroundColor Red }

Write-Host ""
Write-Host "Done. You can close this window." -ForegroundColor Green
Write-Host "Tell the agent it is finished; it will verify and continue." -ForegroundColor Green
