#!/bin/bash
set -euo pipefail

PERSIST="/workspace"
export OLLAMA_MODEL="${OLLAMA_MODEL:-miramax}"

# One-time bootstrap marker
if [ ! -f "$PERSIST/.bootstrapped" ]; then
  /bin/bash /app/scripts/bootstrap.sh
  touch "$PERSIST/.bootstrapped"
fi

# ---------- Start Ollama ----------
echo "[startup] Starting Ollama..."
(ollama serve >/tmp/ollama.log 2>&1) &
sleep 3
echo "[startup] Ensuring model '$OLLAMA_MODEL' is available..."
ollama list | grep -q "$OLLAMA_MODEL" || ollama pull "$OLLAMA_MODEL" || true

# ---------- Start ComfyUI ----------
echo "[startup] Starting ComfyUI..."
source "$PERSIST/ComfyUI/venv/bin/activate"
( python "$PERSIST/ComfyUI/main.py" --listen 0.0.0.0 --port 8188 >/tmp/comfyui.log 2>&1 ) &
deactivate

# ---------- Start FastAPI Backend ----------
echo "[startup] Starting FastAPI backend..."
source "$PERSIST/venv/bin/activate"
( uvicorn backend.app.main:app --host 0.0.0.0 --port 5000 >/tmp/api.log 2>&1 ) &
deactivate

sleep 2
echo "[startup] Services launched. Tailing logs..."
tail -F /tmp/ollama.log /tmp/comfyui.log /tmp/api.log
