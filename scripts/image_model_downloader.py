#!/usr/bin/env python3
import os, sys, shutil
from pathlib import Path
from huggingface_hub import snapshot_download

PERSIST = Path("/workspace")
COMFY = PERSIST / "ComfyUI"
MODELS = COMFY / "models"

# Ensure directories
for sub in ["unet","style_models","text_encoders","vae","loras","upscale_models"]:
    (MODELS / sub).mkdir(parents=True, exist_ok=True)

if not os.getenv("HUGGINGFACE_TOKEN"):
    print("[warn] HUGGINGFACE_TOKEN not set; gated models may fail.", file=sys.stderr)

SOURCES = {
    "unet": [
        ("black-forest-labs/FLUX.1-dev", ["flux1-dev.safetensors", "flux1-dev.safetensors.index.json"]),
    ],
    "vae": [
        ("black-forest-labs/FLUX.1-dev", ["ae.safetensors"]),
    ],
    "style_models": [
        ("black-forest-labs/FLUX.1-Redux-dev", ["flux1-redux-dev.safetensors"]),
    ],
    "text_encoders": [
        ("comfyanonymous/flux_text_encoders", None),
    ],
    "loras": [
        ("DavidBalo/Extreme_Detailer", None),
        ("DavidBalo/Hyper_Realism_Lora_by_aidma", None),
        ("Ripplelinks/Anti-Beard-Lora", None),
    ],
    "upscale_models": [
        ("Ripplelinks/Flux_Models", None),
        ("sczhou/CodeFormer", None),
    ],
}

def fetch(repo_id, dest, patterns=None):
    cache_dir = snapshot_download(repo_id=repo_id, token=os.getenv("HUGGINGFACE_TOKEN"), resume_download=True)
    cp = Path(cache_dir)
    if patterns is None:
        files = [p for p in cp.rglob("*") if p.is_file()]
    else:
        files = []
        for pat in patterns:
            files.extend(list(cp.rglob(pat)))
    for p in files:
        out = dest / p.name
        if not out.exists():
            shutil.copy2(p, out)

def main():
    for subdir, entries in SOURCES.items():
        target = (MODELS / subdir)
        for repo_id, patterns in entries:
            print(f"[dl] {subdir} <- {repo_id}")
            fetch(repo_id, target, patterns)
    print("[ok] Model download complete. Restart ComfyUI to load.")

if __name__ == "__main__":
    main()
