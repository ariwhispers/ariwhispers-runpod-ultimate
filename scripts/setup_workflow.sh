#!/bin/bash
set -euo pipefail
if [ $# -lt 1 ]; then
  echo "usage: $0 /path/to/sirio_consistency.json [GEMINI_API_KEY]"
  exit 1
fi
SRC="$1"
GEMINI="${2:-${GEMINI_API_KEY:-}}"
DEST="/workspace/ComfyUI/workflows"
mkdir -p "$DEST"
cp "$SRC" "$DEST/sirio_consistency.json"
echo "[✓] Workflow placed at $DEST/sirio_consistency.json"
if [ -n "$GEMINI" ]; then
  echo "export GEMINI_API_KEY=$GEMINI" >> /etc/profile
  echo "[i] GEMINI_API_KEY persisted in /etc/profile for shell sessions."
fi
