@echo off
setlocal

echo [1/5] Checking for uv...
where uv >nul 2>nul
if %errorlevel%==0 goto uv_ready
echo uv not found. Installing uv (no admin rights required)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
if errorlevel 1 goto fail
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
:uv_ready

echo [2/5] Installing Python and dependencies (first run may take a few minutes)...
uv sync
if errorlevel 1 goto fail

echo [3/5] Checking NER model...
if not exist "models\heBERT_NER_onnx\model.onnx" (
    echo ERROR: models\heBERT_NER_onnx\model.onnx not found.
    echo Make sure the models folder was copied together with the project.
    goto fail
)

echo [4/5] Initializing database...
uv run python -m app.setup db
if errorlevel 1 goto fail

if not exist ".env" (
    copy .env.example .env >nul
    echo.
    echo A new .env file was created. Notepad will now open -
    echo paste your API keys, save the file, and close Notepad.
    notepad .env
)

echo [5/5] Creating desktop shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1"
if errorlevel 1 goto fail

echo.
echo Setup complete. Double-click the desktop icon to open the application.
echo (Developers: start.bat still runs it with a console.)
pause
exit /b 0

:fail
echo.
echo Setup failed. See the error message above.
pause
exit /b 1
