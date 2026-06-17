@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "APP_PATH=%PROJECT_ROOT%apps\quality_ui\quality_ui\app.py"
set "UI_URL=http://localhost:8501"
set "PYTHON_EXE=%PROJECT_ROOT%.venv\Scripts\python.exe"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%PROJECT_ROOT%"

echo [oj-quality-platform] Project root: %PROJECT_ROOT%

if not exist "%PYTHON_EXE%" (
    echo [error] Project virtual environment was not found.
    echo Expected: %PYTHON_EXE%
    echo.
    echo Please run setup_quality_ui.bat once, then start this launcher again.
    pause
    exit /b 1
)

echo [python] %PYTHON_EXE%
"%PYTHON_EXE%" --version

if not exist "%APP_PATH%" (
    echo [error] Streamlit app not found:
    echo %APP_PATH%
    pause
    exit /b 1
)

echo [check] Checking Python imports...
"%PYTHON_EXE%" -c "import streamlit, quality_agent, quality_ui" >nul 2>nul
if errorlevel 1 (
    echo [error] Dependencies are missing in the project .venv.
    echo Please run setup_quality_ui.bat once, then start this launcher again.
    pause
    exit /b 1
)

if /I "%~1"=="--check" (
    echo [ok] Startup check passed.
    exit /b 0
)

if /I "%~1"=="--reuse" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul
    if not errorlevel 1 (
        echo [info] Port 8501 is already in use. Opening the existing UI URL.
        start "" "%UI_URL%"
        exit /b 0
    )
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >nul 2>nul
if not errorlevel 1 (
    echo [restart] Port 8501 is in use. Stopping existing Quality Agent UI processes...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = (Resolve-Path '%PROJECT_ROOT%').Path; Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*streamlit*quality_ui*' -and $_.CommandLine -like ('*' + $root + '*') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    timeout /t 2 >nul
)

echo [start] Launching Quality Agent UI...
echo [url] %UI_URL%
start "Open Quality Agent UI" cmd /c "timeout /t 3 >nul && start "" "%UI_URL%""
"%PYTHON_EXE%" -m streamlit run "%APP_PATH%" --server.port 8501 --server.address localhost

echo.
echo [done] Streamlit has stopped.
pause
