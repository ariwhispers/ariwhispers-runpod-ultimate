#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, Optional
import requests

# --- config / helpers ---------------------------------------------------------

RUNPOD_API_BASE = os.getenv("RUNPOD_API_BASE", "https://rest.runpod.io").rstrip("/")
API_TIMEOUT = 30

def _auth_headers() -> Dict[str, str]:
    token = os.getenv("RUNPOD_API_KEY", "").strip()
    if not token:
        print("[launch_pod] FATAL: RUNPOD_API_KEY is not set in env.", file=sys.stderr)
        sys.exit(1)
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

def _get(url: str, **kwargs) -> requests.Response:
    r = requests.get(url, headers=_auth_headers(), timeout=API_TIMEOUT, **kwargs)
    if r.status_code >= 400:
        try:
            print(f"[launch_pod] ERROR: GET {url}: {r.status_code} {r.reason}; response={r.text}")
        except Exception:
            print(f"[launch_pod] ERROR: GET {url}: {r.status_code} {r.reason}")
    r.raise_for_status()
    return r

def _post(url: str, payload: Dict[str, Any]) -> requests.Response:
    r = requests.post(url, headers=_auth_headers(), json=payload, timeout=API_TIMEOUT)
    if r.status_code >= 400:
        try:
            print(f"[launch_pod] ERROR: POST {url}: {r.status_code} {r.reason}; response={r.text}")
        except Exception:
            print(f"[launch_pod] ERROR: POST {url}: {r.status_code} {r.reason}")
    r.raise_for_status()
    return r

def _patch(url: str, payload: Dict[str, Any]) -> requests.Response:
    r = requests.patch(url, headers=_auth_headers(), json=payload, timeout=API_TIMEOUT)
    if r.status_code >= 400:
        try:
            print(f"[launch_pod] ERROR: PATCH {url}: {r.status_code} {r.reason}; response={r.text}")
        except Exception:
            print(f"[launch_pod] ERROR: PATCH {url}: {r.status_code} {r.reason}")
    r.raise_for_status()
    return r

# --- runpod REST v1 helpers (pods) -------------------------------------------
# NOTE: The important bit is using https://rest.runpod.io as base.
# These paths match the v1 REST pod API commonly used by RunPod's Secure Cloud.

def api_list_pods(name: Optional[str] = None) -> Dict[str, Any]:
    url = f"{RUNPOD_API_BASE}/v1/pods"
    params = {}
    if name:
        params["name"] = name
    r = _get(url, params=params)
    return r.json()

def api_create_pod(spec: Dict[str, Any]) -> Dict[str, Any]:
    url = f"{RUNPOD_API_BASE}/v1/pods"
    r = _post(url, spec)
    return r.json()

def api_get_pod(pod_id: str) -> Dict[str, Any]:
    url = f"{RUNPOD_API_BASE}/v1/pods/{pod_id}"
    r = _get(url)
    return r.json()

def api_start_pod(pod_id: str) -> Dict[str, Any]:
    url = f"{RUNPOD_API_BASE}/v1/pods/{pod_id}/start"
    r = _post(url, {})
    return r.json()

def api_stop_pod(pod_id: str) -> Dict[str, Any]:
    url = f"{RUNPOD_API_BASE}/v1/pods/{pod_id}/stop"
    r = _post(url, {})
    return r.json()

# --- logic --------------------------------------------------------------------

def load_spec(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def find_existing_pod(name: str) -> Optional[Dict[str, Any]]:
    try:
        pods = api_list_pods(name=name)
    except requests.HTTPError as e:
        print(f"[launch_pod] FATAL: list pods failed: {e}", file=sys.stderr)
        raise
    # Expect either {"pods":[...]} or a plain list depending on API; handle both
    items = pods.get("pods", pods if isinstance(pods, list) else [])
    for p in items:
        if (p.get("name") or "").strip() == name:
            return p
    return None

def wait_until_ready(pod_id: str, timeout_secs: int) -> bool:
    deadline = time.time() + timeout_secs
    last_state = None
    while time.time() < deadline:
        info = api_get_pod(pod_id)
        state = (info.get("status") or info.get("state") or "").lower()
        if state != last_state:
            print(f"[launch_pod] pod {pod_id} state={state}")
            last_state = state
        if state in {"running", "ready", "started"}:
            return True
        time.sleep(5)
    return False

def check_ports_stub(pod: Dict[str, Any]) -> None:
    # If you need to actively probe mapped ports, do it here (SSH/Jupyter).
    # Keeping this as a stub to avoid false negatives during creation.
    pass

def upsert_pod(spec: Dict[str, Any], name: str, wait_ready: int, check_ports: bool) -> Dict[str, Any]:
    existing = find_existing_pod(name)
    if existing:
        pod_id = existing.get("id") or existing.get("podId") or existing.get("pod_id")
        print(f"[launch_pod] existing pod found: {name} -> {pod_id}")
        # ensure it's started
        try:
            api_start_pod(pod_id)
        except requests.HTTPError:
            # if already running, it's fine
            pass
        if wait_ready and not wait_until_ready(pod_id, wait_ready):
            raise SystemExit(f"[launch_pod] FATAL: pod {pod_id} did not become ready within {wait_ready}s")
        if check_ports:
            check_ports_stub(existing)
        # return fresh info
        return api_get_pod(pod_id)

    print(f"[launch_pod] creating pod '{name}'")
    created = api_create_pod(spec)
    pod_id = created.get("id") or created.get("podId") or created.get("pod_id")
    if not pod_id:
        raise SystemExit(f"[launch_pod] FATAL: create returned no pod id: {created}")
    # start (some clusters auto-start on create; calling start is safe)
    try:
        api_start_pod(pod_id)
    except requests.HTTPError:
        pass
    if wait_ready and not wait_until_ready(pod_id, wait_ready):
        raise SystemExit(f"[launch_pod] FATAL: pod {pod_id} did not become ready within {wait_ready}s")
    if check_ports:
        check_ports_stub(created)
    return api_get_pod(pod_id)

# --- CLI ----------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--config", default="infra/runpod/pod_config.json")
    parser.add_argument("--wait-ready", type=int, default=600)
    parser.add_argument("--check-ports", type=lambda s: str(s).lower() == "true", default=True)
    parser.add_argument("--idle-minutes", type=int, default=30)  # reserved for your own use if needed
    args = parser.parse_args()

    spec = load_spec(args.config)
    # make sure name in spec matches the provided name (source of truth is CLI)
    spec["name"] = args.name

    try:
        pod_info = upsert_pod(spec, name=args.name, wait_ready=args.wait_ready, check_ports=args.check_ports)
    except requests.HTTPError as e:
        print(f"[launch_pod] FATAL: {e}", file=sys.stderr)
        return 1

    pod_id = pod_info.get("id") or pod_info.get("podId") or pod_info.get("pod_id") or ""
    if pod_id:
        # emit to GitHub Actions output
        gha_out = os.environ.get("GITHUB_OUTPUT")
        if gha_out:
            with open(gha_out, "a", encoding="utf-8") as fh:
                fh.write(f"POD_ID={pod_id}\n")
        print(f"[launch_pod] POD_ID={pod_id}")
        return 0
    else:
        print(f"[launch_pod] WARN: could not determine pod id from: {pod_info}", file=sys.stderr)
        return 0

if __name__ == "__main__":
    sys.exit(main())
