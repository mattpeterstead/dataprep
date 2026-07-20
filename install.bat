@echo off
if /i not "%~1"=="--inner" (
  start "DataPrep Installer" cmd /c ""%~f0" --inner"
  exit /b
)
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

set "LOG_DIR=%CD%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "LOG=%LOG_DIR%\install_log.txt"
set "REQ=%CD%\requirements.txt"
> "%LOG%" echo DataPrep Installer

echo.
echo =================================
echo Selecting Python
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Selecting Python
>> "%LOG%" echo =================================

set "PY_CMD="
for %%V in (3.11 3.12 3.13 3.10) do (
  if not defined PY_CMD (
    py -%%V --version >> "%LOG%" 2>&1
    if not errorlevel 1 set "PY_CMD=py -%%V"
  )
)
if not defined PY_CMD (
  py -3 --version >> "%LOG%" 2>&1
  if not errorlevel 1 set "PY_CMD=py -3"
)
if not defined PY_CMD goto :no_supported_python

%PY_CMD% -c "import sys; print(sys.version); raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)" >> "%LOG%" 2>&1
if errorlevel 1 set "PY_CMD="
if not defined PY_CMD goto :no_supported_python
goto :python_selected

:no_supported_python
echo [ERROR] Could not find a supported Python via py launcher.
echo Install Python 3.11, 3.12, or 3.13 and run this installer again.
>> "%LOG%" echo [ERROR] Could not find a supported Python via py launcher.
goto :fail

:python_selected

echo Using: %PY_CMD%
>> "%LOG%" echo Using: %PY_CMD%

echo.
echo =================================
echo Checking app files
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Checking app files
>> "%LOG%" echo =================================

if not exist "imageprep_simple.py" (
  echo [ERROR] imageprep_simple.py not found in %CD%
  >> "%LOG%" echo [ERROR] imageprep_simple.py not found in %CD%
  goto :fail
)

if not exist "videoprep.py" (
  echo [ERROR] videoprep.py not found in %CD%
  >> "%LOG%" echo [ERROR] videoprep.py not found in %CD%
  goto :fail
)

if not exist "%REQ%" (
  echo [ERROR] requirements.txt not found in %CD%
  >> "%LOG%" echo [ERROR] requirements.txt not found in %CD%
  goto :fail
)

if exist ".venv" (
  echo.
  echo Existing virtual environment found:
  echo %CD%\.venv
  echo.
  echo The installer needs to remove and recreate it to update dependencies.
  choice /C YN /N /M "Remove the existing .venv and continue? [Y/N] "
  if errorlevel 2 (
    echo.
    echo Installation cancelled. Existing .venv was left unchanged.
    >> "%LOG%" echo Installation cancelled by user. Existing .venv was left unchanged.
    pause
    exit /b 1
  )
  echo Removing old virtual environment...
  >> "%LOG%" echo Removing old virtual environment after user confirmation...
  rmdir /s /q ".venv" >> "%LOG%" 2>&1
)

echo.
echo =================================
echo Creating virtual environment
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Creating virtual environment
>> "%LOG%" echo =================================

%PY_CMD% -m venv ".venv" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Failed to create virtual environment.
  >> "%LOG%" echo [ERROR] Failed to create virtual environment.
  goto :fail
)

set "PY_EXE=%CD%\.venv\Scripts\python.exe"
set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
if not exist "%PY_EXE%" (
  echo [ERROR] Virtual environment Python not found: %PY_EXE%
  >> "%LOG%" echo [ERROR] Virtual environment Python not found: %PY_EXE%
  goto :fail
)

echo.
echo =================================
echo Updating pip tooling
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Updating pip tooling
>> "%LOG%" echo =================================

echo This may download pip, setuptools, and wheel updates.
echo Started: %DATE% %TIME%
>> "%LOG%" echo Started pip tooling update: %DATE% %TIME%
"%PY_EXE%" -m pip --log "%LOG%" install --upgrade --progress-bar on pip setuptools wheel
set "STEP_EXIT=%ERRORLEVEL%"
echo Finished: %DATE% %TIME%
>> "%LOG%" echo Finished pip tooling update: %DATE% %TIME% with code %STEP_EXIT%
if not "%STEP_EXIT%"=="0" (
  echo [ERROR] Failed to update pip tooling.
  >> "%LOG%" echo [ERROR] Failed to update pip tooling.
  goto :fail
)

echo.
echo =================================
echo Selecting PyTorch build
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Selecting PyTorch build
>> "%LOG%" echo =================================

set "TORCH_BACKEND=cpu"
set "TORCH_INDEX=https://download.pytorch.org/whl/cpu"
set "TORCH_REASON=No supported GPU runtime detected."

powershell -NoProfile -ExecutionPolicy Bypass -Command "$backend = 'cpu'; $reason = 'No NVIDIA GPU runtime detected.'; $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue; if ($smi) { $out = & nvidia-smi 2>$null; $match = $out | Select-String -Pattern 'CUDA Version:\s*([0-9]+)\.([0-9]+)' | Select-Object -First 1; if ($match) { $major = [int]$match.Matches[0].Groups[1].Value; $minor = [int]$match.Matches[0].Groups[2].Value; $cuda = [version]::new($major, $minor); if ($cuda -ge [version]'12.8') { $backend = 'cu128'; $reason = 'NVIDIA driver reports CUDA ' + $cuda + '; selecting CUDA 12.8 PyTorch wheels.' } elseif ($cuda -ge [version]'12.6') { $backend = 'cu126'; $reason = 'NVIDIA driver reports CUDA ' + $cuda + '; selecting CUDA 12.6 PyTorch wheels.' } elseif ($cuda -ge [version]'11.8') { $backend = 'cu118'; $reason = 'NVIDIA driver reports CUDA ' + $cuda + '; selecting CUDA 11.8 PyTorch wheels.' } else { $reason = 'NVIDIA driver reports CUDA ' + $cuda + ', which is older than the supported PyTorch CUDA wheels used by this installer.' } } else { $reason = 'nvidia-smi was found, but CUDA version could not be read.' } }; Write-Output ('TORCH_BACKEND=' + $backend); Write-Output ('TORCH_REASON=' + $reason)" > "%TEMP%\dataset_forge_torch_detect.txt"
for /f "usebackq tokens=1,* delims==" %%A in ("%TEMP%\dataset_forge_torch_detect.txt") do (
  if "%%A"=="TORCH_BACKEND" set "TORCH_BACKEND=%%B"
  if "%%A"=="TORCH_REASON" set "TORCH_REASON=%%B"
)
del "%TEMP%\dataset_forge_torch_detect.txt" >nul 2>&1

if /i "%TORCH_BACKEND%"=="cu128" set "TORCH_INDEX=https://download.pytorch.org/whl/cu128"
if /i "%TORCH_BACKEND%"=="cu126" set "TORCH_INDEX=https://download.pytorch.org/whl/cu126"
if /i "%TORCH_BACKEND%"=="cu118" set "TORCH_INDEX=https://download.pytorch.org/whl/cu118"
if /i "%TORCH_BACKEND%"=="cpu" set "TORCH_INDEX=https://download.pytorch.org/whl/cpu"

echo PyTorch build: %TORCH_BACKEND%
echo %TORCH_REASON%
echo Wheel index: %TORCH_INDEX%
>> "%LOG%" echo PyTorch build: %TORCH_BACKEND%
>> "%LOG%" echo %TORCH_REASON%
>> "%LOG%" echo Wheel index: %TORCH_INDEX%

echo.
echo =================================
echo Installing PyTorch
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Installing PyTorch
>> "%LOG%" echo =================================

echo Downloading and installing PyTorch for this machine.
if /i "%TORCH_BACKEND%"=="cpu" (
  echo No supported NVIDIA CUDA runtime was detected, so CPU PyTorch will be installed.
) else (
  echo GPU PyTorch selected. This download is large, but Qwen3-VL should be able to use CUDA afterwards.
)
echo Started: %DATE% %TIME%
>> "%LOG%" echo Started PyTorch install: %DATE% %TIME%
"%PY_EXE%" -m pip --log "%LOG%" install --progress-bar on torch torchvision torchaudio --index-url "%TORCH_INDEX%"
set "STEP_EXIT=%ERRORLEVEL%"
echo Finished: %DATE% %TIME%
>> "%LOG%" echo Finished PyTorch install: %DATE% %TIME% with code %STEP_EXIT%
if not "%STEP_EXIT%"=="0" (
  echo [ERROR] Failed to install PyTorch.
  >> "%LOG%" echo [ERROR] Failed to install PyTorch.
  goto :fail
)

"%PY_EXE%" -c "import torch; print('torch:', torch.__version__); print('torch cuda build:', torch.version.cuda); print('cuda available:', torch.cuda.is_available()); print('cuda device count:', torch.cuda.device_count()); print('cuda device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none')" >> "%LOG%" 2>&1
if errorlevel 1 goto :torch_verify_failed
echo PyTorch verification:
"%PY_EXE%" -c "import torch; print('  torch:', torch.__version__); print('  cuda build:', torch.version.cuda); print('  cuda available:', torch.cuda.is_available()); print('  cuda device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none')"
if /i "%TORCH_BACKEND%"=="cpu" goto :after_cuda_access_check
"%PY_EXE%" -c "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)"
if errorlevel 1 goto :cuda_access_warning
goto :after_cuda_access_check

:torch_verify_failed
echo [ERROR] PyTorch verification failed.
>> "%LOG%" echo [ERROR] PyTorch verification failed.
goto :fail

:cuda_access_warning
echo [WARNING] A CUDA PyTorch build was installed, but PyTorch cannot access CUDA.
echo [WARNING] Check the NVIDIA driver if Qwen3-VL still runs on CPU.
>> "%LOG%" echo [WARNING] CUDA build installed but torch.cuda.is_available() is false.

:after_cuda_access_check

echo.
echo =================================
echo Installing app dependencies
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Installing app dependencies
>> "%LOG%" echo =================================

echo Downloading and installing Python packages from requirements.txt.
echo Large packages include transformers, onnxruntime, rembg, and model helper libraries.
echo Pip will show download progress, file sizes, and transfer speed below.
echo Started: %DATE% %TIME%
>> "%LOG%" echo Started requirements install: %DATE% %TIME%
"%PY_EXE%" -m pip --log "%LOG%" install --progress-bar on -r "%REQ%"
set "STEP_EXIT=%ERRORLEVEL%"
echo Finished: %DATE% %TIME%
>> "%LOG%" echo Finished requirements install: %DATE% %TIME% with code %STEP_EXIT%
if not "%STEP_EXIT%"=="0" (
  echo [ERROR] Failed to install dependencies from requirements.txt.
  >> "%LOG%" echo [ERROR] Failed to install dependencies from requirements.txt.
  goto :fail
)

echo Installing the LaMa inpainting helper without downgrading shared image libraries.
"%PY_EXE%" -m pip --log "%LOG%" install --progress-bar on --no-deps simple-lama-inpainting
if errorlevel 1 (
  echo [ERROR] Failed to install the LaMa inpainting helper.
  >> "%LOG%" echo [ERROR] Failed to install the LaMa inpainting helper.
  goto :fail
)

echo.
echo =================================
echo Installing optional WhisperX video transcription backend
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Installing optional WhisperX video transcription backend
>> "%LOG%" echo =================================

echo WhisperX is optional and large. If this step fails, the rest of DataPrep can still run.
echo Started: %DATE% %TIME%
>> "%LOG%" echo Started optional WhisperX install: %DATE% %TIME%
"%PY_EXE%" -m pip --log "%LOG%" install --progress-bar on --upgrade-strategy only-if-needed ctranslate2 faster-whisper omegaconf pandas nltk pyannote.audio torchcodec
if errorlevel 1 (
  echo [WARNING] WhisperX dependency installation failed.
  >> "%LOG%" echo [WARNING] WhisperX dependency installation failed.
)
"%PY_EXE%" -m pip --log "%LOG%" install --progress-bar on --no-deps git+https://github.com/m-bain/whisperx.git
set "WHISPERX_EXIT=%ERRORLEVEL%"
echo Finished: %DATE% %TIME%
>> "%LOG%" echo Finished optional WhisperX install: %DATE% %TIME% with code %WHISPERX_EXIT%
if not "%WHISPERX_EXIT%"=="0" (
  echo [WARNING] WhisperX installation failed. Video WhisperX captions will show an install error until WhisperX is installed.
  >> "%LOG%" echo [WARNING] WhisperX installation failed; continuing installation.
)

echo.
echo =================================
echo Verifying Qwen3-VL local dependencies
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Verifying Qwen3-VL local dependencies
>> "%LOG%" echo =================================

"%PY_EXE%" -c "import transformers, torch, accelerate, qwen_vl_utils; from transformers import AutoModelForImageTextToText, AutoProcessor; major = int(transformers.__version__.split('.')[0]); assert major < 5, 'Qwen3-VL local mode currently requires transformers 4.57.x, not ' + transformers.__version__; print('qwen3-vl local deps: ok'); print('transformers:', transformers.__version__)" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Qwen3-VL local dependency verification failed.
  >> "%LOG%" echo [ERROR] Qwen3-VL local dependency verification failed.
  goto :fail
)

echo.
echo =================================
echo Preparing folders
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Preparing folders
>> "%LOG%" echo =================================

if not exist "tools" mkdir "tools" >> "%LOG%" 2>&1
if not exist "tools\koboldcpp" mkdir "tools\koboldcpp" >> "%LOG%" 2>&1
if not exist "models" mkdir "models" >> "%LOG%" 2>&1
if not exist "models\joycaption_gguf" mkdir "models\joycaption_gguf" >> "%LOG%" 2>&1
if not exist "models\joycaption_gguf\downloads" mkdir "models\joycaption_gguf\downloads" >> "%LOG%" 2>&1

echo.
echo =================================
echo Downloading KoboldCpp
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Downloading KoboldCpp
>> "%LOG%" echo =================================

set "KOBOLD_URL=https://github.com/LostRuins/koboldcpp/releases/latest/download/koboldcpp.exe"
set "KOBOLD_EXE=%CD%\tools\koboldcpp\koboldcpp.exe"

echo Downloading KoboldCpp executable.
echo PowerShell will show download progress below when the server reports file size.
echo Started: %DATE% %TIME%
>> "%LOG%" echo Started KoboldCpp download: %DATE% %TIME%
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference = 'Continue'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $sw = [Diagnostics.Stopwatch]::StartNew(); Write-Host 'Downloading from: %KOBOLD_URL%'; Invoke-WebRequest -Uri '%KOBOLD_URL%' -OutFile '%KOBOLD_EXE%'; $sw.Stop(); Write-Host ('KoboldCpp download finished in {0:hh\:mm\:ss}' -f $sw.Elapsed)"
set "STEP_EXIT=%ERRORLEVEL%"
echo Finished: %DATE% %TIME%
>> "%LOG%" echo Finished KoboldCpp download: %DATE% %TIME% with code %STEP_EXIT%
if not "%STEP_EXIT%"=="0" (
  echo [ERROR] Failed to download KoboldCpp.
  >> "%LOG%" echo [ERROR] Failed to download KoboldCpp.
  goto :fail
)

if not exist "%KOBOLD_EXE%" (
  echo [ERROR] KoboldCpp executable missing after download.
  >> "%LOG%" echo [ERROR] KoboldCpp executable missing after download.
  goto :fail
)

echo.
echo =================================
echo Writing default GGUF config
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Writing default GGUF config
>> "%LOG%" echo =================================

if not exist "%CD%\settings" mkdir "%CD%\settings"

> "%CD%\models\joycaption_gguf\README.txt" echo Put downloaded JoyCaption Beta One GGUF files here if you fetch them manually.
>> "%CD%\models\joycaption_gguf\README.txt" echo The app can later download a selected quantization model and mmproj automatically.

> "%CD%\settings\joycaption_gguf_defaults.json" echo {
>> "%CD%\settings\joycaption_gguf_defaults.json" echo   "repo_id": "concedo/llama-joycaption-beta-one-hf-llava-mmproj-gguf",
>> "%CD%\settings\joycaption_gguf_defaults.json" echo   "mmproj_file": "llama-joycaption-beta-one-llava-mmproj-model-f16.gguf",
>> "%CD%\settings\joycaption_gguf_defaults.json" echo   "models": {
>> "%CD%\settings\joycaption_gguf_defaults.json" echo     "Q4_K": "Llama-Joycaption-Beta-One-Hf-Llava-Q4_K.gguf",
>> "%CD%\settings\joycaption_gguf_defaults.json" echo     "Q8_0": "Llama-Joycaption-Beta-One-Hf-Llava-Q8_0.gguf",
>> "%CD%\settings\joycaption_gguf_defaults.json" echo     "F16": "Llama-Joycaption-Beta-One-Hf-Llava-F16.gguf"
>> "%CD%\settings\joycaption_gguf_defaults.json" echo   },
>> "%CD%\settings\joycaption_gguf_defaults.json" echo   "koboldcpp_exe": "tools\\koboldcpp\\koboldcpp.exe",
>> "%CD%\settings\joycaption_gguf_defaults.json" echo   "model_dir": "models\\joycaption_gguf",
>> "%CD%\settings\joycaption_gguf_defaults.json" echo   "api_host": "127.0.0.1",
>> "%CD%\settings\joycaption_gguf_defaults.json" echo   "api_port": 5001
>> "%CD%\settings\joycaption_gguf_defaults.json" echo }

echo.
echo =================================
echo Verifying environment
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Verifying environment
>> "%LOG%" echo =================================

"%PY_EXE%" -c "import sys, flask, PIL, requests, huggingface_hub, psutil, numpy, onnxruntime, pillow_avif, rembg, transformers, torch, torchvision, accelerate, qwen_vl_utils, safetensors, timm, einops, cv2, sentencepiece, google.protobuf; assert int(transformers.__version__.split('.')[0]) < 5, 'Unsupported transformers version: ' + transformers.__version__; print('python:', sys.version); print('flask:', flask.__version__); print('pillow:', PIL.__version__); print('requests:', requests.__version__); print('hf_hub:', huggingface_hub.__version__); print('psutil:', psutil.__version__); print('numpy:', numpy.__version__); print('onnxruntime:', onnxruntime.__version__); print('rembg: ok'); print('opencv:', cv2.__version__); print('pillow_avif: ok'); print('transformers:', transformers.__version__); print('torch:', torch.__version__); print('torch cuda build:', torch.version.cuda); print('torch cuda available:', torch.cuda.is_available()); print('torch cuda device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none'); print('torchvision:', torchvision.__version__); print('accelerate:', accelerate.__version__); print('qwen_vl_utils: ok'); print('sentencepiece:', sentencepiece.__version__); print('protobuf:', google.protobuf.__version__); print('safetensors:', safetensors.__version__); print('timm:', timm.__version__); print('einops: ok')" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] Verification failed.
  >> "%LOG%" echo [ERROR] Verification failed.
  goto :fail
)

echo.
echo =================================
echo Writing launchers
echo =================================
>> "%LOG%" echo.
>> "%LOG%" echo =================================
>> "%LOG%" echo Writing launchers
>> "%LOG%" echo =================================

set "DATAPREP_INSTALLER_FILE=%~f0"
set "DATAPREP_INSTALL_ROOT=%CD%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$installer = $env:DATAPREP_INSTALLER_FILE; $root = $env:DATAPREP_INSTALL_ROOT; $source = [IO.File]::ReadAllText($installer); function Write-Launcher([string]$name, [string]$begin, [string]$end) { $pattern = '(?ms)^:: ' + [regex]::Escape($begin) + '\r?\n(.*?)^:: ' + [regex]::Escape($end) + '(?:\r?\n|$)'; $match = [regex]::Match($source, $pattern); if (-not $match.Success) { throw ('Embedded launcher template not found: ' + $name) }; $text = $match.Groups[1].Value -replace '\r?\n', [Environment]::NewLine; [IO.File]::WriteAllText((Join-Path $root $name), $text, [Text.UTF8Encoding]::new($false)) }; Write-Launcher 'start.bat' 'DATAPREP_START_BAT_BEGIN' 'DATAPREP_START_BAT_END'; Write-Launcher 'start_video.bat' 'DATAPREP_START_VIDEO_BAT_BEGIN' 'DATAPREP_START_VIDEO_BAT_END'" >> "%LOG%" 2>&1
set "LAUNCHER_EXIT=%ERRORLEVEL%"
set "DATAPREP_INSTALLER_FILE="
set "DATAPREP_INSTALL_ROOT="
if not "%LAUNCHER_EXIT%"=="0" (
  echo [ERROR] Failed to create Windows launchers.
  >> "%LOG%" echo [ERROR] Failed to create Windows launchers.
  goto :fail
)

if not exist "%CD%\start.bat" (
  echo [ERROR] start.bat was not created.
  >> "%LOG%" echo [ERROR] start.bat was not created.
  goto :fail
)
if not exist "%CD%\start_video.bat" (
  echo [ERROR] start_video.bat was not created.
  >> "%LOG%" echo [ERROR] start_video.bat was not created.
  goto :fail
)
>> "%LOG%" echo Created: %CD%\start.bat
>> "%LOG%" echo Created: %CD%\start_video.bat

echo.
echo ==========================================
echo INSTALL SUMMARY: OK
echo Environment: %CD%\.venv
echo Requirements: %REQ%
echo KoboldCpp: %KOBOLD_EXE%
echo GGUF defaults: %CD%\settings\joycaption_gguf_defaults.json
echo Image launcher: %CD%\start.bat
echo Video launcher: %CD%\start_video.bat
echo Log: %LOG%
echo ==========================================
echo The installer does not download captioning model weights.
echo The app will download selected JoyCaption and Qwen3-VL models later when first used.
echo.
echo Press any key to close this installer window.
pause
exit /b 0

:fail
echo.
echo ==========================================
echo INSTALL SUMMARY: FAILED
echo Environment: %CD%\.venv
echo Log: %LOG%
echo ==========================================
if exist "%LOG%" (
  echo.
  echo Last install log lines:
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Content -Path '%LOG%' -Tail 120"
)
pause
exit /b 1

:: DATAPREP_START_BAT_BEGIN
@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
set "APP=%ROOT%imageprep_simple.py"
set "URL=http://127.0.0.1:5000/"
set "PORT=5000"
set "LABEL=Image mode"

pushd "%ROOT%" >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] Could not open application folder:
  echo %ROOT%
  echo.
  pause
  exit /b 1
)

title DataPrep - %LABEL%

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

powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } exit 1" >nul 2>&1
if "%ERRORLEVEL%"=="0" (
  echo.
  echo DataPrep %LABEL% already appears to be running.
  echo Opening %URL%
  echo.
  start "" "%URL%"
  popd >nul
  exit /b 0
)

echo.
echo Starting DataPrep %LABEL%...
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
echo DataPrep %LABEL% exited with error code %EXITCODE%.
echo.
pause

popd >nul
exit /b %EXITCODE%
:: DATAPREP_START_BAT_END
:: DATAPREP_START_VIDEO_BAT_BEGIN
@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"
set "APP=%ROOT%videoprep.py"
set "URL=http://127.0.0.1:5002/"
set "PORT=5002"
set "LABEL=Video"

pushd "%ROOT%" >nul 2>&1
if errorlevel 1 (
  echo.
  echo [ERROR] Could not open application folder:
  echo %ROOT%
  echo.
  pause
  exit /b 1
)

title DataPrep - %LABEL%

if not exist "%PYTHON%" (
  echo.
  echo [ERROR] Missing virtual environment:
  echo %ROOT%.venv
  echo.
  echo Run install.bat first.
  echo.
  pause
  popd >nul
  exit /b 1
)

if not exist "%APP%" (
  echo.
  echo [ERROR] Missing app file:
  echo %APP%
  echo.
  pause
  popd >nul
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
  popd >nul
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort %PORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } exit 1" >nul 2>&1
if "%ERRORLEVEL%"=="0" (
  echo.
  echo DataPrep %LABEL% already appears to be running.
  echo Opening %URL%
  echo.
  start "" "%URL%"
  popd >nul
  exit /b 0
)

echo.
echo Starting DataPrep %LABEL%...
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
echo DataPrep %LABEL% exited with error code %EXITCODE%.
echo.
pause

popd >nul
exit /b %EXITCODE%
:: DATAPREP_START_VIDEO_BAT_END
