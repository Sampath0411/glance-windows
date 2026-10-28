@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0.."
title Glance for Windows

REM ---------------------------------------------------------------- bootstrap
if exist ".venv\Scripts\python.exe" goto :check_models

echo ==========================================================
echo   First run: creating the Python environment.
echo   This downloads onnxruntime and opencv, takes a minute.
echo ==========================================================
echo.
py -3.12 -m venv .venv
if errorlevel 1 (
    echo.
    echo Could not create the virtual environment.
    echo Install Python 3.12 from python.org and tick "Add to PATH".
    goto :fail
)
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo.
    echo Could not install dependencies. Check your internet connection.
    goto :fail
)

:check_models
if exist "models\buffalo_s\w600k_mbf.onnx" goto :ready
if exist "models\buffalo_s\det_500m.onnx" goto :ready

echo.
echo Model files are missing from models\buffalo_s\
echo Need: w600k_mbf.onnx and det_500m.onnx
echo.
echo Download buffalo_s.zip from:
echo   https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_s.zip
echo and extract it into models\buffalo_s\
echo.
goto :fail

:ready
if not "%~1"=="" goto :dispatch

REM -------------------------------------------------------------------- menu
:menu
cls
echo ==========================================================
echo    Glance for Windows
echo ==========================================================
echo.
echo    1  Enroll my face
echo    2  Live match  (light liveness - Glance default)
echo    3  Live match  (heavy liveness)
echo    4  Self-test   (headless, writes debug images)
echo    5  Accuracy report
echo    6  Liveness tests
echo    7  Benchmark this machine
echo    8  Check / repair enrollment
echo    9  Exit
echo.
set "choice="
set /p "choice=Choose 1-9: "
if not defined choice goto :menu
if "%choice%"=="1" goto :do_enroll
if "%choice%"=="2" goto :do_live_light
if "%choice%"=="3" goto :do_live_heavy
if "%choice%"=="4" goto :do_selftest
if "%choice%"=="5" goto :do_eval
if "%choice%"=="6" goto :do_test
if "%choice%"=="7" goto :do_bench
if "%choice%"=="8" goto :do_tune
if "%choice%"=="9" goto :end
echo.
echo "%choice%" is not one of the options.
timeout /t 2 >nul
goto :menu

:do_enroll
cls
echo Enter a name for this face profile, e.g. your first name.
set "who="
set /p "who=Name: "
if not defined who goto :menu
echo.
echo Look at the camera. Turn your head slowly left and right.
echo Press SPACE to grab a sample (do 8-12), Q to save, ESC to cancel.
echo.
".venv\Scripts\python.exe" -m glance.cli.app enroll --name "%who%"
echo.
pause
goto :menu

:do_live_light
cls
".venv\Scripts\python.exe" -m glance.cli.app live --liveness light
if errorlevel 1 (
    echo.
    echo The live view exited with an error - see the message above.
)
echo.
pause
goto :menu

:do_live_heavy
cls
".venv\Scripts\python.exe" -m glance.cli.app live --liveness heavy
if errorlevel 1 (
    echo.
    echo The live view exited with an error - see the message above.
)
echo.
pause
goto :menu

:do_selftest
cls
".venv\Scripts\python.exe" -m glance.cli.app selftest --frames 10
echo.
pause
goto :menu

:do_eval
cls
".venv\Scripts\python.exe" -m glance.cli.eval_recognition
echo.
pause
goto :menu

:do_test
cls
echo === CLI parsing ===
".venv\Scripts\python.exe" -m tests.test_cli
echo.
echo === Liveness cues ===
".venv\Scripts\python.exe" -m tests.test_liveness
echo.
pause
goto :menu

:do_bench
cls
".venv\Scripts\python.exe" -m glance.cli.app bench --frames 15
echo.
pause
goto :menu

:do_tune
cls
echo This checks whether your enrolled template still matches what the camera
echo sees right now, and offers to repair it if it does not.
echo.
echo Look at the camera and stay in frame for a few seconds.
echo.
".venv\Scripts\python.exe" -m glance.cli.tune_enrollment
echo.
pause
goto :menu

REM --------------------------------------------------- command-line form
:dispatch
if /i "%~1"=="eval" (
    ".venv\Scripts\python.exe" -m glance.cli.eval_recognition
    goto :finish
)
if /i "%~1"=="test" (
    ".venv\Scripts\python.exe" -m tests.test_cli
    ".venv\Scripts\python.exe" -m tests.test_liveness
    goto :finish
)
if /i "%~1"=="selftest" (
    ".venv\Scripts\python.exe" -m glance.cli.app selftest --frames 10
    goto :finish
)
if /i "%~1"=="tune" (
    ".venv\Scripts\python.exe" -m glance.cli.tune_enrollment %2 %3
    goto :finish
)
".venv\Scripts\python.exe" -m glance.cli.app %*
goto :finish

:finish
if errorlevel 1 (
    echo.
    echo That command reported an error - see the message above.
)
exit /b %errorlevel%

:fail
echo.
pause
exit /b 1

:end
exit /b 0
