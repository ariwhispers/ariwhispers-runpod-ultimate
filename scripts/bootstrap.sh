#!/bin/bash
set -euo pipefail

PERSIST="/workspace"
mkdir -p "$PERSIST"

echo "[bootstrap] Updating apt and installing basics..."
apt-get update -y && apt-get install -y curl git build-essential wget python3-venv ffmpeg

# ---------- OLLAMA ----------
if ! command -v ollama >/dev/null 2>&1; then
  echo "[bootstrap] Installing Ollama..."
  curl -fsSL https://ollama.com/install.sh | sh
fi

mkdir -p "$PERSIST/ollama"
if [ ! -L /root/.ollama ]; then
  rm -rf /root/.ollama || true
  ln -s "$PERSIST/ollama" /root/.ollama
fi

# ---------- COMFYUI ----------
if [ ! -d "$PERSIST/ComfyUI" ]; then
  echo "[bootstrap] Cloning ComfyUI..."
  git clone https://github.com/comfyanonymous/ComfyUI.git "$PERSIST/ComfyUI"
fi
python3 -m venv "$PERSIST/ComfyUI/venv"
source "$PERSIST/ComfyUI/venv/bin/activate"
pip install --upgrade pip wheel
pip install -r "$PERSIST/ComfyUI/requirements.txt" || true
deactivate

# Models folder (persistent)
mkdir -p "$PERSIST/models/checkpoints" "$PERSIST/models/loras" "$PERSIST/models/controlnet"

# ---------- BACKEND PYTHON VENV ----------
if [ ! -d "$PERSIST/venv" ]; then
  python3 -m venv "$PERSIST/venv"
fi
source "$PERSIST/venv/bin/activate"
pip install --upgrade pip wheel
pip install fastapi uvicorn requests pydantic python-dotenv huggingface_hub
deactivate

echo "[bootstrap] Done. Heavy installs cached under $PERSIST"
