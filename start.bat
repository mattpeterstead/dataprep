@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
set "SETTINGS_DIR=%ROOT%settings"
set "LAST_FILE=%SETTINGS_DIR%\.dataset_forge_last_app"

pushd "%ROOT%" >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] Could not open application folder:
  echo %ROOT%
  echo.
  pause
  exit /b 1
)

if not exist "%SETTINGS_DIR%" mkdir "%SETTINGS_DIR%" >nul 2>&1

set "MODE=image"
if exist "%LAST_FILE%" (
  set /p MODE=<"%LAST_FILE%"
)
if /i not "%MODE%"=="video" if /i not "%MODE%"=="simple" set "MODE=image"

if /i "%MODE%"=="video" (
  set "APP=%ROOT%videoprep.py"
  set "URL=http://127.0.0.1:5001/"
  set "PORT=5001"
  set "LABEL=Video"
) else if /i "%MODE%"=="simple" (
  set "APP=%ROOT%imageprep_simple.py"
  set "URL=http://127.0.0.1:5000/"
  set "PORT=5000"
  set "LABEL=Image Simple"
) else (
  set "APP=%ROOT%imageprep.py"
  set "URL=http://127.0.0.1:5000/"
  set "PORT=5000"
  set "LABEL=Image Advanced"
)

title Dataset Forge - %LABEL%

if not exist "%PYTHON%" (
  echo.
  echo [ERROR] Missing virtual environment:
  echo %ROOT%.venv
  echo.
  echo Run install.bat first.
  echo.
  pause
  exit /b 1
)

if not exist "%APP%" (
  echo.
  echo [ERROR] Missing app file:
  echo %APP%
  echo.
  pause
  exit /b 1
)

"%PYTHON%" -B -c "import flask, PIL, requests, psutil, numpy, huggingface_hub, onnxruntime, pillow_avif, transformers, torch, torchvision, accelerate, qwen_vl_utils, safetensors, timm, einops, cv2, sentencepiece, google.protobuf" >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] Required Python packages are missing from:
  echo %ROOT%.venv
  echo.
  echo Run install.bat and allow it to recreate the virtual environment.
  echo.
  pause
  exit /b 1
)

>"%LAST_FILE%" echo %MODE%

powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } exit 1" >nul 2>&1
if "%ERRORLEVEL%"=="0" (
  echo.
  echo Dataset Forge %LABEL% already appears to be running.
  echo Opening %URL%
  echo.
  start "" "%URL%"
  popd >nul
  exit /b 0
)

echo.
echo Starting Dataset Forge %LABEL%...
echo URL: %URL%
echo.

start "" powershell -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "Start-Sleep -Seconds 2; Start-Process '%URL%'"
"%PYTHON%" "%APP%"
set "EXITCODE=%ERRORLEVEL%"

if "%EXITCODE%"=="0" (
  popd >nul
  exit /b 0
)

echo.
echo Dataset Forge %LABEL% exited with error code %EXITCODE%.
echo.
pause

popd >nul
exit /b %EXITCODE%
