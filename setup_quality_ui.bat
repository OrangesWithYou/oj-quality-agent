@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "VENV_DIR=%PROJECT_ROOT%.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

cd /d "%PROJECT_ROOT%"

echo [oj-quality-platform] Project root: %PROJECT_ROOT%

if /I "%~1"=="--check" (
    if not exist "%PYTHON_EXE%" (
        echo [error] Project virtual environment was not found.
        echo Expected: %PYTHON_EXE%
        exit /b 1
    )

    echo [python] %PYTHON_EXE%
    "%PYTHON_EXE%" --version

    "%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if errorlevel 1 (
        echo [error] Python 3.10 or newer is required.
        exit /b 1
    )

    echo [check] Checking setup dependencies...
    "%PYTHON_EXE%" -c "import streamlit, langchain, langgraph, langfuse, quality_agent, quality_ui" >nul 2>nul
    if errorlevel 1 (
        echo [error] Dependencies are missing in the project .venv.
        exit /b 1
    )

    echo [ok] Setup check passed.
    exit /b 0
)

if not exist "%PYTHON_EXE%" (
    echo [setup] Creating project virtual environment at:
    echo %VENV_DIR%

    call :create_venv
    if errorlevel 1 (
        echo [error] Could not create project virtual environment.
        echo Please install Python 3.10-3.12, then run this script again.
        pause
        exit /b 1
    )
)

echo [python] %PYTHON_EXE%
"%PYTHON_EXE%" --version

"%PYTHON_EXE%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 (
    echo [error] Python 3.10 or newer is required.
    echo Please recreate .venv with Python 3.10-3.12.
    pause
    exit /b 1
)

echo [setup] Installing project dependencies...
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 (
    echo [error] pip upgrade failed.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m pip install -e ".[agent]"
if errorlevel 1 (
    echo [error] Dependency installation failed.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -c "import streamlit, langchain, langgraph, langfuse, quality_agent, quality_ui" >nul 2>nul
if errorlevel 1 (
    echo [error] Import check failed after installation.
    pause
    exit /b 1
)

echo [ok] Setup finished. You can now double-click start_quality_ui.bat.
pause
exit /b 0

:create_venv
echo [setup] Trying Python launcher: py -3.12
py -3.12 -m venv "%VENV_DIR%" >nul 2>nul
if not errorlevel 1 exit /b 0

echo [setup] Trying Python launcher: py -3.11
py -3.11 -m venv "%VENV_DIR%" >nul 2>nul
if not errorlevel 1 exit /b 0

echo [setup] Trying Python launcher: py -3.10
py -3.10 -m venv "%VENV_DIR%" >nul 2>nul
if not errorlevel 1 exit /b 0

echo [setup] Trying python from PATH
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1

python -m venv "%VENV_DIR%"
exit /b %errorlevel%
