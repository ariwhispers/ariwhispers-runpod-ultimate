#!/usr/bin/env python3
import argparse, json, os, sys, time
from pathlib import Path

import requests

API_BASE = os.getenv("RUNPOD_API_BASE", "https://rest.runpod.io").rstrip("/")
API_KEY = os.getenv("RUNPOD_API_KEY")

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {API_KEY}" if API_KEY else "",
    "Content-Type": "application/json",
})

def _url(path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"{API_BASE}/v1{path}"

def _req(method: str, path: str, **kwargs):
    url = _url(path)
    r = SESSION.request(method, url, timeout=60, **kwargs)
    try:
        payload = r.json()
    except Exception:
        payload = r.text
    if not r.ok:
        print(f"[launch_pod] ERROR: {method} {url} -> {r.status_code}; response={payload}", file=sys.stderr)
        r.raise_for_status()
    return payload

def list_pods():
    page = 1
    pods = []
    while True:
        data = _req("GET", "/pods", params={"page": page})
        items = data.get("data", []) if isinstance(data, dict) else data
        if not items:
            break
        pods.extend(items)
        meta = data.get("meta") if isinstance(data, dict) else None
        if not meta or page >= int(meta.get("totalPages", page)):
            break
        page += 1
    return pods

def get_pod_by_name(name: str):
    for p in list_pods():
        if p.get("name") == name:
            return p
    return None

def get_pod(pod_id: str):
    return _req("GET", f"/pods/{pod_id}")

def create_pod_from_config(config_path: Path):
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    return _req("POST", "/pods", json=cfg)

def start_pod(pod_id: str):
    return _req("POST", f"/pods/{pod_id}/start")

def stop_pod(pod_id: str):
    return _req("POST", f"/pods/{pod_id}/stop")

def status_is_running(pod) -> bool:
    st = (pod.get("desiredStatus") or pod.get("status") or "").upper()
    return st == "RUNNING"

def wait_until_running(pod_id: str, timeout_secs: int) -> dict:
    deadline = time.time() + timeout_secs
    last = None
    while time.time() < deadline:
        last = get_pod(pod_id)
        st = (last.get("desiredStatus") or last.get("status") or "").upper()
        print(f"[launch_pod] waiting: {pod_id} -> {st}")
        if status_is_running(last):
            return last
        if st in {"ERROR", "TERMINATED"}:
            raise RuntimeError(f"Pod entered terminal state: {st}")
        time.sleep(5)
    raise TimeoutError(f"Timed out waiting for pod {pod_id} to be RUNNING")

def main():
    if not API_KEY:
        print("[launch_pod] FATAL: RUNPOD_API_KEY is not set", file=sys.stderr)
        sys.exit(1)

    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--wait-ready", type=int, default=600)
    ap.add_argument("--check-ports", type=lambda s: s.lower() in {"1","true","yes"}, default=True)
    ap.add_argument("--config", default="infra/runpod/pod_config.json")
    args = ap.parse_args()

    print(f"[launch_pod] Using API base: {API_BASE}")
    if API_BASE.startswith("https://api.runpod.io"):
        print("[launch_pod] WARNING: api.runpod.io is not the REST v1 base; use https://rest.runpod.io", file=sys.stderr)

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        print(f"[launch_pod] FATAL: config file not found: {cfg_path}", file=sys.stderr)
        sys.exit(1)

    pod = get_pod_by_name(args.name)
    if pod:
        pid = pod["id"]
        st = (pod.get("desiredStatus") or pod.get("status") or "").upper()
        print(f"[launch_pod] Found existing pod: {args.name} id={pid} status={st}")
        if st != "RUNNING":
            print(f"[launch_pod] Starting pod {pid}…")
            start_pod(pid)
    else:
        print(f"[launch_pod] Creating new pod: {args.name}")
        created = create_pod_from_config(cfg_path)
        pod = created.get("data") if isinstance(created, dict) and "data" in created else created
        pid = pod["id"]
        print(f"[launch_pod] Created pod id={pid}")

    pod = wait_until_running(pid, args.wait_ready)

    if args.check_ports:
        host = pod.get("host") or pod.get("publicIp") or "(no-host)"
        ports = pod.get("ports") or []
        print(f"[launch_pod] Host: {host}")
        print(f"[launch_pod] Ports: {ports}")

    line = f"POD_ID={pid}"
    print(line)
    gh_out = os.getenv("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"[launch_pod] FATAL: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"[launch_pod] FATAL: {e}", file=sys.stderr)
        sys.exit(1)
