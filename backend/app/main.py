import os, json, time, uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests

app = FastAPI()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
COMFY_URL  = os.getenv("COMFY_URL",  "http://127.0.0.1:8188")
WORKFLOW_DIR = os.getenv("WORKFLOW_DIR", "/workspace/ComfyUI/workflows")

class ChatRequest(BaseModel):
    prompt: str
    model: str | None = None

class CCImageRequest(BaseModel):
    workflow: str = "sirio_consistency.json"
    prompt_text: str | None = None
    ref_image_path: str | None = None

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.post("/chat")
def chat(req: ChatRequest):
    model = req.model or os.getenv("OLLAMA_MODEL", "miramax")
    try:
        r = requests.post(f"{OLLAMA_URL}/api/generate", json={"model": model, "prompt": req.prompt}, timeout=120)
        r.raise_for_status()
        data = r.json()
        return {"model": model, "text": data.get("response", "") or r.text}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ollama error: {e}")

def comfy_queue_prompt(graph):
    r = requests.post(f"{COMFY_URL}/prompt", json={"prompt": graph}, timeout=30)
    r.raise_for_status()
    return r.json()

@app.post("/generate-image/cc")
def generate_image_cc(req: CCImageRequest):
    wf_path = os.path.join(WORKFLOW_DIR, req.workflow)
    if not os.path.exists(wf_path):
        raise HTTPException(status_code=400, detail=f"Workflow not found: {wf_path}")
    try:
        graph = json.load(open(wf_path, "r", encoding="utf-8"))
        # Minimal patching: replace placeholders if present
        if req.prompt_text:
            # naive: search for nodes with 'text' or 'prompt' keys
            for _, node in graph.get("nodes", {}).items():
                for key in ("text", "prompt", "positive"):
                    if key in node.get("inputs", {}):
                        node["inputs"][key] = req.prompt_text
        if req.ref_image_path and os.path.exists(req.ref_image_path):
            # For loaders expecting a file path in inputs named 'image' / 'images'
            for _, node in graph.get("nodes", {}).items():
                if "image" in node.get("inputs", {}):
                    node["inputs"]["image"] = req.ref_image_path
                if "images" in node.get("inputs", {}):
                    node["inputs"]["images"] = [req.ref_image_path]
        resp = comfy_queue_prompt(graph)
        return {"ok": True, "queue": resp}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ComfyUI workflow error: {e}")
