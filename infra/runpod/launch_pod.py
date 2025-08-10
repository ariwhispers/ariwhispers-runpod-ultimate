# infra/runpod/launch_pod.py
import argparse, json, os, sys, time
import requests

BASE_URL = "https://api.runpod.io/v2"
TIMEOUT = 30

def die(msg: str, code: int = 1):
    print(f"[launch_pod] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)

def load_pod_config(path="infra/runpod/pod_config.json") -> dict:
    try:
        with open(path, "rb") as f:
            raw = f.read()
        # Strip BOM if present
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as e:
        die(f"Invalid JSON in {os.path.abspath(path)}: {e}")
    except FileNotFoundError:
        die(f"Missing config file at {path}")

def api(method, path, token, **kwargs):
    url = f"{BASE_URL}{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        r = requests.request(method, url, headers=headers, timeout=TIMEOUT, **kwargs)
    except requests.RequestException as e:
        die(f"HTTP error: {e}")
    if r.status_code >= 400:
        # Try to surface API error message
        try:
            msg = r.json()
        except Exception:
            msg = r.text
        die(f"{method} {path} -> {r.status_code}: {msg}")
    return r

def find_pod_by_name(name, token):
    # Best effort: if /pods?name= is supported use it; else fetch and filter
    r = api("GET", f"/pods?name={name}", token)
    try:
        items = r.json().get("data") or r.json()
    except Exception:
        items = []
    # If API returns plain list
    if isinstance(items, dict) and "pods" in items:
        items = items["pods"]
    if not isinstance(items, list):
        # Fallback: GET /pods and filter locally
        r2 = api("GET", "/pods", token)
        try:
            items = r2.json().get("data") or r2.json()
        except Exception:
            items = []
        if isinstance(items, dict) and "pods" in items:
            items = items["pods"]
        if not isinstance(items, list):
            items = []
    for p in items:
        if (p.get("name") or "").strip() == name:
            return p
    return None

def normalize_body(cfg, override_name=None):
    body = dict(cfg)
    if override_name:
        body["name"] = override_name
    # Some users store ports as list; API accepts string or list — keep as-is.
    return body

def print_outputs(pod):
    pod_id = pod.get("id") or pod.get("podId") or ""
    name = pod.get("name") or ""
    print(f"[launch_pod] OK: pod '{name}' -> id={pod_id}")
    # GitHub Actions outputs (if available)
    gha_out = os.getenv("GITHUB_OUTPUT")
    if gha_out:
        with open(gha_out, "a", encoding="utf-8") as f:
            f.write(f"pod_id={pod_id}\n")
            f.write(f"pod_name={name}\n")
    # Also emit a Notice for easy visibility
    print(f"::notice title=RunPod::POD_ID={pod_id}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", help="Pod name to upsert", required=True)
    args = ap.parse_args()

    token = os.getenv("RUNPOD_API_KEY")
    if not token:
        die("RUNPOD_API_KEY is not set")

    cfg = load_pod_config()
    body = normalize_body(cfg, override_name=args.name)

    print("Final pod request body (truncated to 2000 chars):")
    print(json.dumps(body, indent=2)[:2000])

    # Find existing pod by name
    existing = find_pod_by_name(args.name, token)

    if existing and (existing.get("id") or existing.get("podId")):
        pod_id = existing.get("id") or existing.get("podId")
        # Update in place
        updated = api("PATCH", f"/pods/{pod_id}", token, json=body).json()
        # Some APIs return wrapper {data:{...}}
        pod = updated.get("data") or updated
        print_outputs(pod if isinstance(pod, dict) else {"id": pod_id, "name": args.name})
        return 0
    else:
        # Create new pod
        created = api("POST", "/pods", token, json=body).json()
        pod = created.get("data") or created
        print_outputs(pod if isinstance(pod, dict) else {"name": args.name})
        return 0

if __name__ == "__main__":
    sys.exit(main())
