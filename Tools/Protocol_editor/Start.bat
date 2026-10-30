@echo off
cd /d "%~dp0"
where pythonw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pythonw.exe "%~dp0protocol_editor.py" %*
    exit /b
)
where pyw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pyw.exe -3 "%~dp0protocol_editor.py" %*
    exit /b
)
python "%~dp0protocol_editor.py" %*
if errorlevel 1 pause
