#!/bin/bash
set -euo pipefail
PERSIST="/workspace/ComfyUI"
CN="$PERSIST/custom_nodes"
mkdir -p "$CN"

# WAS Node Suite
if [ ! -d "$CN/ComfyUI-WAS-Node-Suite" ]; then
  git clone https://github.com/WASasquatch/ComfyUI-WAS-Node-Suite.git "$CN/ComfyUI-WAS-Node-Suite"
fi

# rgthree Power Nodes
if [ ! -d "$CN/rgthree-comfy" ]; then
  git clone https://github.com/rgthree/rgthree-comfy.git "$CN/rgthree-comfy"
fi

# Requirements (best-effort)
source "/workspace/ComfyUI/venv/bin/activate"
pip install -r "$CN/ComfyUI-WAS-Node-Suite/requirements.txt" || true
pip install -r "$CN/rgthree-comfy/requirements.txt" || true
deactivate

echo "[ok] Custom nodes installed."
