@echo off
:: Photo Culling App — launcher (Windows)
::
:: Normal use:  start.cmd          -> production mode (pre-built frontend)
:: Dev use:     start.cmd --dev    -> Vite dev server on :5173 (hot reload)

setlocal
set ROOT=%~dp0
if "%ROOT:~-1%"=="\" set ROOT=%ROOT:~0,-1%

:: ── Python venv ──────────────────────────────────────────────────────────────
set VENV=%ROOT%\venv
if not exist "%VENV%" (
    echo Creating virtual environment...
    python -m venv "%VENV%"
)

call "%VENV%\Scripts\activate.bat"
pip install -q -r "%ROOT%\requirements.txt"

:: ── Launch ────────────────────────────────────────────────────────────────────
python "%ROOT%\launcher.py" %*
