#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
LOG="$LOG_DIR/install_log.txt"
REQ="$ROOT/requirements.txt"
mkdir -p "$LOG_DIR"
: > "$LOG"

log() {
  echo "$*"
  echo "$*" >> "$LOG"
}

section() {
  echo
  log "================================="
  log "$1"
  log "================================="
}

fail() {
  echo
  log "=========================================="
  log "INSTALL SUMMARY: FAILED"
  log "Environment: $ROOT/.venv"
  log "Log: $LOG"
  log "=========================================="
  if [[ -f "$LOG" ]]; then
    echo
    echo "Last install log lines:"
    tail -n 120 "$LOG" || true
  fi
  exit 1
}

run_logged() {
  "$@" 2>&1 | tee -a "$LOG"
  local rc=${PIPESTATUS[0]}
  return "$rc"
}

print_tk_help() {
  log "Install the Tk bindings for your Python version with your distribution package manager."
  log "Package names vary by distribution. Common names include: python3-tk, python3-tkinter, python-tkinter, tk."
}

section "Selecting Python"

PY_CMD=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" - <<'PY' >> "$LOG" 2>&1
import sys
print(sys.version)
raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)
PY
    then
      PY_CMD="$candidate"
      break
    fi
  fi
done

if [[ -z "$PY_CMD" ]]; then
  log "[ERROR] Could not find Python 3.10, 3.11, 3.12, or 3.13."
  log "Install a supported Python version and run this installer again."
  fail
fi

log "Using: $PY_CMD"

section "Checking app files"

[[ -f "$ROOT/imageprep.py" ]] || { log "[ERROR] imageprep.py not found in $ROOT"; fail; }
[[ -f "$ROOT/videoprep.py" ]] || { log "[ERROR] videoprep.py not found in $ROOT"; fail; }
[[ -f "$REQ" ]] || { log "[ERROR] requirements.txt not found in $ROOT"; fail; }

if [[ -d "$ROOT/.venv" ]]; then
  echo
  echo "Existing virtual environment found:"
  echo "$ROOT/.venv"
  echo
  read -r -p "Remove the existing .venv and continue? [y/N] " answer
  case "$answer" in
    [Yy]|[Yy][Ee][Ss])
      log "Removing old virtual environment after user confirmation..."
      rm -rf "$ROOT/.venv"
      ;;
    *)
      log "Installation cancelled. Existing .venv was left unchanged."
      exit 1
      ;;
  esac
fi

section "Creating virtual environment"

run_logged "$PY_CMD" -m venv "$ROOT/.venv" || { log "[ERROR] Failed to create virtual environment."; fail; }

PY_EXE="$ROOT/.venv/bin/python"
PIP_EXE="$PY_EXE -m pip"
export HF_HUB_DISABLE_SYMLINKS_WARNING=1

[[ -x "$PY_EXE" ]] || { log "[ERROR] Virtual environment Python not found: $PY_EXE"; fail; }

if ! "$PY_EXE" - <<'PY' >> "$LOG" 2>&1
import tkinter
PY
then
  log "[ERROR] Python tkinter is not available."
  print_tk_help
  fail
fi

section "Updating pip tooling"

log "This may download pip, setuptools, and wheel updates."
log "Started: $(date)"
run_logged "$PY_EXE" -m pip install --upgrade --progress-bar on pip setuptools wheel || {
  log "[ERROR] Failed to update pip tooling."
  fail
}
log "Finished: $(date)"

section "Selecting PyTorch build"

TORCH_BACKEND="cpu"
TORCH_INDEX="https://download.pytorch.org/whl/cpu"
TORCH_REASON="No NVIDIA GPU runtime detected."

if command -v nvidia-smi >/dev/null 2>&1; then
  CUDA_VERSION="$(nvidia-smi 2>/dev/null | sed -nE 's/.*CUDA Version: ([0-9]+)\.([0-9]+).*/\1.\2/p' | head -n 1 || true)"
  if [[ -n "$CUDA_VERSION" ]]; then
    CUDA_MAJOR="${CUDA_VERSION%%.*}"
    CUDA_MINOR="${CUDA_VERSION#*.}"
    if (( CUDA_MAJOR > 12 || (CUDA_MAJOR == 12 && CUDA_MINOR >= 8) )); then
      TORCH_BACKEND="cu128"
      TORCH_INDEX="https://download.pytorch.org/whl/cu128"
      TORCH_REASON="NVIDIA driver reports CUDA $CUDA_VERSION; selecting CUDA 12.8 PyTorch wheels."
    elif (( CUDA_MAJOR > 12 || (CUDA_MAJOR == 12 && CUDA_MINOR >= 6) )); then
      TORCH_BACKEND="cu126"
      TORCH_INDEX="https://download.pytorch.org/whl/cu126"
      TORCH_REASON="NVIDIA driver reports CUDA $CUDA_VERSION; selecting CUDA 12.6 PyTorch wheels."
    elif (( CUDA_MAJOR > 11 || (CUDA_MAJOR == 11 && CUDA_MINOR >= 8) )); then
      TORCH_BACKEND="cu118"
      TORCH_INDEX="https://download.pytorch.org/whl/cu118"
      TORCH_REASON="NVIDIA driver reports CUDA $CUDA_VERSION; selecting CUDA 11.8 PyTorch wheels."
    else
      TORCH_REASON="NVIDIA driver reports CUDA $CUDA_VERSION, which is older than the supported PyTorch CUDA wheels used by this installer."
    fi
  else
    TORCH_REASON="nvidia-smi was found, but CUDA version could not be read."
  fi
fi

log "PyTorch build: $TORCH_BACKEND"
log "$TORCH_REASON"
log "Wheel index: $TORCH_INDEX"

section "Installing PyTorch"

log "Downloading and installing PyTorch for this machine."
if [[ "$TORCH_BACKEND" == "cpu" ]]; then
  log "No supported NVIDIA CUDA runtime was detected, so CPU PyTorch will be installed."
else
  log "GPU PyTorch selected. This download is large, but Qwen3-VL should be able to use CUDA afterwards."
fi
log "Started: $(date)"
run_logged "$PY_EXE" -m pip install --progress-bar on torch torchvision torchaudio --index-url "$TORCH_INDEX" || {
  log "[ERROR] Failed to install PyTorch."
  fail
}
log "Finished: $(date)"

run_logged "$PY_EXE" - <<'PY' || { log "[ERROR] PyTorch verification failed."; fail; }
import torch
print("torch:", torch.__version__)
print("torch cuda build:", torch.version.cuda)
print("cuda available:", torch.cuda.is_available())
print("cuda device count:", torch.cuda.device_count())
print("cuda device:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none")
PY

if [[ "$TORCH_BACKEND" != "cpu" ]]; then
  if ! "$PY_EXE" - <<'PY' >> "$LOG" 2>&1
import sys, torch
sys.exit(0 if torch.cuda.is_available() else 1)
PY
  then
    log "[WARNING] A CUDA PyTorch build was installed, but PyTorch cannot access CUDA."
    log "[WARNING] Check the NVIDIA driver if Qwen3-VL still runs on CPU."
  fi
fi

section "Installing app dependencies"

log "Downloading and installing Python packages from requirements.txt."
log "Large packages include transformers, onnxruntime, rembg, and model helper libraries."
log "Started: $(date)"
run_logged "$PY_EXE" -m pip install --progress-bar on -r "$REQ" || {
  log "[ERROR] Failed to install dependencies from requirements.txt."
  fail
}
log "Finished: $(date)"

section "Installing optional WhisperX video transcription backend"

log "WhisperX is optional and large. If this step fails, the rest of DataPrep can still run."
log "Started: $(date)"
run_logged "$PY_EXE" -m pip install --progress-bar on --upgrade-strategy only-if-needed ctranslate2 faster-whisper omegaconf pandas nltk pyannote.audio torchcodec || {
  log "[WARNING] WhisperX dependency installation failed."
}

if command -v git >/dev/null 2>&1; then
  run_logged "$PY_EXE" -m pip install --progress-bar on --no-deps git+https://github.com/m-bain/whisperx.git || {
    log "[WARNING] WhisperX installation failed. Video WhisperX captions will show an install error until WhisperX is installed."
  }
else
  log "[WARNING] git was not found. Skipping WhisperX package install from GitHub."
fi
log "Finished: $(date)"

section "Verifying Qwen3-VL local dependencies"

if ! run_logged "$PY_EXE" - <<'PY'
import transformers, torch, accelerate, qwen_vl_utils
from transformers import AutoModelForImageTextToText, AutoProcessor
major = int(transformers.__version__.split(".")[0])
assert major < 5, "Qwen3-VL local mode currently requires transformers 4.57.x, not " + transformers.__version__
print("qwen3-vl local deps: ok")
print("transformers:", transformers.__version__)
PY
then
  log "[ERROR] Qwen3-VL local dependency verification failed."
  fail
fi

section "Preparing folders"

mkdir -p "$ROOT/tools/koboldcpp" "$ROOT/models/joycaption_gguf/downloads" "$ROOT/settings"

section "Downloading KoboldCpp"

KOBOLD_URL="https://github.com/LostRuins/koboldcpp/releases/latest/download/koboldcpp-linux-x64"
KOBOLD_EXE="$ROOT/tools/koboldcpp/koboldcpp"

log "Downloading KoboldCpp Linux executable."
log "Started: $(date)"
if command -v curl >/dev/null 2>&1; then
  run_logged curl -L --fail --progress-bar "$KOBOLD_URL" -o "$KOBOLD_EXE" || {
    log "[ERROR] Failed to download KoboldCpp."
    fail
  }
elif command -v wget >/dev/null 2>&1; then
  run_logged wget -O "$KOBOLD_EXE" "$KOBOLD_URL" || {
    log "[ERROR] Failed to download KoboldCpp."
    fail
  }
else
  log "[ERROR] Neither curl nor wget was found. Install one of them and run this installer again."
  fail
fi
chmod +x "$KOBOLD_EXE"
log "Finished: $(date)"

[[ -x "$KOBOLD_EXE" ]] || { log "[ERROR] KoboldCpp executable missing after download."; fail; }

section "Writing default GGUF config"

cat > "$ROOT/models/joycaption_gguf/README.txt" <<'TXT'
Put downloaded JoyCaption Beta One GGUF files here if you fetch them manually.
The app can later download a selected quantization model and mmproj automatically.
TXT

cat > "$ROOT/settings/joycaption_gguf_defaults.json" <<'JSON'
{
  "repo_id": "concedo/llama-joycaption-beta-one-hf-llava-mmproj-gguf",
  "mmproj_file": "llama-joycaption-beta-one-llava-mmproj-model-f16.gguf",
  "models": {
    "Q4_K": "Llama-Joycaption-Beta-One-Hf-Llava-Q4_K.gguf",
    "Q8_0": "Llama-Joycaption-Beta-One-Hf-Llava-Q8_0.gguf",
    "F16": "Llama-Joycaption-Beta-One-Hf-Llava-F16.gguf"
  },
  "koboldcpp_exe": "tools/koboldcpp/koboldcpp",
  "model_dir": "models/joycaption_gguf",
  "api_host": "127.0.0.1",
  "api_port": 5001
}
JSON

section "Verifying environment"

if ! run_logged "$PY_EXE" - <<'PY'
import sys, flask, PIL, requests, huggingface_hub, psutil, numpy, onnxruntime, pillow_avif, rembg
import transformers, torch, torchvision, accelerate, qwen_vl_utils, safetensors, timm, einops, cv2, sentencepiece, google.protobuf
assert int(transformers.__version__.split(".")[0]) < 5, "Unsupported transformers version: " + transformers.__version__
print("python:", sys.version)
print("flask:", flask.__version__)
print("pillow:", PIL.__version__)
print("requests:", requests.__version__)
print("hf_hub:", huggingface_hub.__version__)
print("psutil:", psutil.__version__)
print("numpy:", numpy.__version__)
print("onnxruntime:", onnxruntime.__version__)
print("rembg: ok")
print("opencv:", cv2.__version__)
print("pillow_avif: ok")
print("transformers:", transformers.__version__)
print("torch:", torch.__version__)
print("torch cuda build:", torch.version.cuda)
print("torch cuda available:", torch.cuda.is_available())
print("torch cuda device:", torch.cuda.get_device_name(0) if torch.cuda.is_available() else "none")
print("torchvision:", torchvision.__version__)
print("accelerate:", accelerate.__version__)
print("qwen_vl_utils: ok")
print("sentencepiece:", sentencepiece.__version__)
print("protobuf:", google.protobuf.__version__)
print("safetensors:", safetensors.__version__)
print("timm:", timm.__version__)
print("einops: ok")
PY
then
  log "[ERROR] Verification failed."
  fail
fi

section "Writing launchers"

cat > "$ROOT/start.sh" <<'DATAPREP_IMAGE_LAUNCHER'
#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON="$ROOT/.venv/bin/python"
SETTINGS_DIR="$ROOT/settings"
LAST_FILE="$SETTINGS_DIR/.dataset_forge_last_app"

mkdir -p "$SETTINGS_DIR"

print_tk_help() {
  echo "Install the Tk bindings for your Python version with your distribution package manager."
  echo "Package names vary by distribution. Common names include: python3-tk, python3-tkinter, python-tkinter, tk."
}

MODE="simple"
if [[ -f "$LAST_FILE" ]]; then
  MODE="$(tr -d '\r\n' < "$LAST_FILE")"
fi

case "${MODE,,}" in
  simple)
    APP="$ROOT/imageprep_simple.py"
    URL="http://127.0.0.1:5000/"
    PORT="5000"
    LABEL="Image Simple"
    ;;
  image)
    APP="$ROOT/imageprep.py"
    URL="http://127.0.0.1:5000/"
    PORT="5000"
    LABEL="Image Advanced"
    ;;
  *)
    MODE="simple"
    APP="$ROOT/imageprep_simple.py"
    URL="http://127.0.0.1:5000/"
    PORT="5000"
    LABEL="Image Simple"
    ;;
esac

if [[ ! -x "$PYTHON" ]]; then
  echo
  echo "[ERROR] Missing virtual environment:"
  echo "$ROOT/.venv"
  echo
  echo "Run ./install.sh first."
  exit 1
fi

if [[ ! -f "$APP" ]]; then
  echo
  echo "[ERROR] Missing app file:"
  echo "$APP"
  exit 1
fi

if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import tkinter
PY
then
  echo
  echo "[ERROR] Python tkinter is not available."
  print_tk_help
  exit 1
fi

if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import flask, PIL, requests, psutil, numpy, huggingface_hub, onnxruntime, pillow_avif
import transformers, torch, torchvision, accelerate, qwen_vl_utils, safetensors, timm, einops, cv2, sentencepiece, google.protobuf
PY
then
  echo
  echo "[ERROR] Required Python packages are missing from:"
  echo "$ROOT/.venv"
  echo
  echo "Run ./install.sh and allow it to recreate the virtual environment."
  exit 1
fi

echo "$MODE" > "$LAST_FILE"

if "$PYTHON" - "$PORT" <<'PY' >/dev/null 2>&1
import socket, sys
with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=0.4):
    pass
PY
then
  echo
  echo "DataPrep $LABEL already appears to be running."
  echo "Opening $URL"
  echo
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  else
    echo "Open this URL in your browser: $URL"
  fi
  exit 0
fi

echo
echo "Starting DataPrep $LABEL..."
echo "URL: $URL"
echo

if command -v xdg-open >/dev/null 2>&1; then
  (
    sleep 2
    xdg-open "$URL" >/dev/null 2>&1 || true
  ) &
else
  echo "Open this URL in your browser after startup: $URL"
fi

exec "$PYTHON" "$APP"
DATAPREP_IMAGE_LAUNCHER

cat > "$ROOT/start_video.sh" <<'DATAPREP_VIDEO_LAUNCHER'
#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PYTHON="$ROOT/.venv/bin/python"
APP="$ROOT/videoprep.py"
URL="http://127.0.0.1:5002/"
PORT="5002"
LABEL="Video"

print_tk_help() {
  echo "Install the Tk bindings for your Python version with your distribution package manager."
  echo "Package names vary by distribution. Common names include: python3-tk, python3-tkinter, python-tkinter, tk."
}

if [[ ! -x "$PYTHON" ]]; then
  echo
  echo "[ERROR] Missing virtual environment:"
  echo "$ROOT/.venv"
  echo
  echo "Run ./install.sh first."
  exit 1
fi

if [[ ! -f "$APP" ]]; then
  echo
  echo "[ERROR] Missing app file:"
  echo "$APP"
  exit 1
fi

if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import tkinter
PY
then
  echo
  echo "[ERROR] Python tkinter is not available."
  print_tk_help
  exit 1
fi

if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import flask, PIL, requests, psutil, numpy, huggingface_hub, onnxruntime, pillow_avif
import transformers, torch, torchvision, accelerate, qwen_vl_utils, safetensors, timm, einops, cv2, sentencepiece, google.protobuf
PY
then
  echo
  echo "[ERROR] Required Python packages are missing from:"
  echo "$ROOT/.venv"
  echo
  echo "Run ./install.sh and allow it to recreate the virtual environment."
  exit 1
fi

if "$PYTHON" - "$PORT" <<'PY' >/dev/null 2>&1
import socket, sys
with socket.create_connection(("127.0.0.1", int(sys.argv[1])), timeout=0.4):
    pass
PY
then
  echo
  echo "DataPrep $LABEL already appears to be running."
  echo "Opening $URL"
  echo
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  else
    echo "Open this URL in your browser: $URL"
  fi
  exit 0
fi

echo
echo "Starting DataPrep $LABEL..."
echo "URL: $URL"
echo

if command -v xdg-open >/dev/null 2>&1; then
  (
    sleep 2
    xdg-open "$URL" >/dev/null 2>&1 || true
  ) &
else
  echo "Open this URL in your browser after startup: $URL"
fi

exec "$PYTHON" "$APP"
DATAPREP_VIDEO_LAUNCHER

chmod +x "$ROOT/start.sh" "$ROOT/start_video.sh"
[[ -x "$ROOT/start.sh" ]] || { log "[ERROR] start.sh was not created as executable."; fail; }
[[ -x "$ROOT/start_video.sh" ]] || { log "[ERROR] start_video.sh was not created as executable."; fail; }
log "Created: $ROOT/start.sh"
log "Created: $ROOT/start_video.sh"

echo
log "=========================================="
log "INSTALL SUMMARY: OK"
log "Environment: $ROOT/.venv"
log "Requirements: $REQ"
log "KoboldCpp: $KOBOLD_EXE"
log "GGUF defaults: $ROOT/settings/joycaption_gguf_defaults.json"
log "Image launcher: $ROOT/start.sh"
log "Video launcher: $ROOT/start_video.sh"
log "Log: $LOG"
log "=========================================="
log "The installer does not download captioning model weights."
log "The app will download selected JoyCaption and Qwen3-VL models later when first used."
