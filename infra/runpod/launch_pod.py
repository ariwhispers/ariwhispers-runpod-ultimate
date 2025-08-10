#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

import requests


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def get_base_url() -> str:
    # Use REST base by default
    return os.environ.get("RUNPOD_API_BASE", "https://rest.runpod.io").rstrip("/")


def get_headers() -> Dict[str, str]:
    api_key = os.environ.get("RUNPOD_API_KEY")
    if not api_key:
        raise RuntimeError("RUNPOD_API_KEY is required (set it as a GitHub Actions secret).")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


# -------------------------
# REST API helpers (/v1/pods)
# -------------------------

def create_pod(spec: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(f"{get_base_url()}/v1/pods", headers=get_headers(), data=json.dumps(spec))
    if not r.ok:
        eprint(f"[launch_pod] ERROR: create pod: {r.status_code} {r.reason}; response={safe_json(r)}")
    r.raise_for_status()
    return r.json()


def list_pods_by_name(name: str) -> List[Dict[str, Any]]:
    r = requests.get(f"{get_base_url()}/v1/pods", headers=get_headers(), params={"name": name})
    if not r.ok:
        eprint(f"[launch_pod] ERROR: list pods: {r.status_code} {r.reason}; response={safe_json(r)}")
    r.raise_for_status()
    data = r.json()
    return data.get("pods", []) if isinstance(data, dict) else []


def get_pod(pod_id: str) -> Dict[str, Any]:
    r = requests.get(f"{get_base_url()}/v1/pods/{pod_id}", headers=get_headers())
    if not r.ok:
        eprint(f"[launch_pod] ERROR: get pod: {r.status_code} {r.reason}; response={safe_json(r)}")
    r.raise_for_status()
    return r.json()


def start_pod(pod_id: str) -> Dict[str, Any]:
    r = requests.post(f"{get_base_url()}/v1/pods/{pod_id}/start", headers=get_headers())
    if not r.ok:
        eprint(f"[launch_pod] ERROR: start pod: {r.status_code} {r.reason}; response={safe_json(r)}")
    r.raise_for_status()
    return r.json()


def stop_pod(pod_id: str) -> Dict[str, Any]:
    r = requests.post(f"{get_base_url()}/v1/pods/{pod_id}/stop", headers=get_headers())
    if not r.ok:
        eprint(f"[launch_pod] ERROR: stop pod: {r.status_code} {r.reason}; response={safe_json(r)}")
    r.raise_for_status()
    return r.json()


def update_pod_autostop(pod_id: str, idle_minutes: int) -> None:
    # Not all accounts/regions support patching runtime config via REST.
    # If unsupported, this will be a no-op with a warning.
    payload = {"idleTimeout": idle_minutes}  # documented as minutes in most setups
    r = requests.patch(f"{get_base_url()}/v1/pods/{pod_id}", headers=get_headers(), data=json.dumps(payload))
    if r.status_code == 404 or r.status_code == 400:
        eprint(f"[launch_pod] WARN: setting idle timeout may be unsupported: {r.status_code} {r.reason}; response={safe_json(r)}")
        return
    if not r.ok:
        eprint(f"[launch_pod] WARN: failed to set idle timeout: {r.status_code} {r.reason}; response={safe_json(r)}")
        return
    eprint(f"[launch_pod] Set idle timeout to {idle_minutes} minutes.")


# -------------------------
# Orchestration
# -------------------------

def upsert_pod(spec: Dict[str, Any], name: str) -> Dict[str, Any]:
    pods = list_pods_by_name(name)
    if pods:
        pod = pods[0]
        eprint(f"[launch_pod] Found existing pod '{name}' (id={pod.get('id')})")
        return pod
    eprint(f"[launch_pod] Creating pod '{name}' (no existing match found)")
    return create_pod(spec)


def wait_until_ready(pod_id: str, timeout_secs: int) -> Dict[str, Any]:
    deadline = time.time() + timeout_secs
    last_status = None
    while time.time() < deadline:
        pod = get_pod(pod_id)
        status = (
            pod.get("runtime", {}).get("state")
            or pod.get("status")
            or pod.get("state")
        )
        if status != last_status:
            eprint(f"[launch_pod] status={status}")
            last_status = status
        if status in ("RUNNING", "READY"):
            return pod
        time.sleep(5)
    raise TimeoutError(f"Pod {pod_id} not ready within {timeout_secs}s")


def check_http_tcp(pod: Dict[str, Any]) -> None:
    """
    Best-effort: print the public endpoints if present.
    Different clusters expose different shapes; we won't fail the job if absent.
    """
    net = pod.get("network") or pod.get("runtime", {}).get("network", {})
    pub_ip = net.get("publicIp") or pod.get("publicIp")
    endpoints = net.get("endpoints") or []
    if pub_ip:
        eprint(f"[launch_pod] publicIp: {pub_ip}")
    if endpoints:
        eprint(f"[launch_pod] endpoints: {json.dumps(endpoints, indent=2)}")
    else:
        eprint("[launch_pod] NOTE: No endpoints field present; skipping port checks.")


def safe_json(r: requests.Response) -> str:
    try:
        return json.dumps(r.json(), indent=2)
    except Exception:
        return r.text


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create or reuse a RunPod pod via REST.")
    p.add_argument("--name", required=True, help="Pod name to create or reuse")
    p.add_argument("--wait-ready", type=int, default=600, help="Seconds to wait for RUNNING/READY")
    p.add_argument("--check-ports", type=str, default="true", help="true/false")
    p.add_argument("--idle-minutes", type=int, default=30, help="Auto-stop after idle minutes (0 to skip)")
    p.add_argument("--config", default="infra/runpod/pod_config.json", help="Path to pod spec JSON")
    return p.parse_args()


def load_spec(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        spec = json.load(f)
    # ensure the spec name matches --name (CLI is source of truth)
    spec["name"] = args.name
    return spec


if __name__ == "__main__":
    try:
        args = parse_args()
        spec = load_spec(args.config)

        # Basic validation on ports shape for REST
        ports = spec.get("ports", [])
        if isinstance(ports, str):
            raise RuntimeError("pod_config.json: 'ports' must be an array like ['8888/http','22/tcp'] for REST.")
        http_ports = [p for p in ports if p.endswith("/http")]
        tcp_ports = [p for p in ports if p.endswith("/tcp")]
        if len(http_ports) > 1 or len(tcp_ports) > 1:
            eprint("[launch_pod] WARN: REST allows at most 1 HTTP and 1 TCP port; extra entries may be ignored by the platform.")

        # Upsert pod
        pod = upsert_pod(spec, args.name)
        pod_id = pod.get("id") or pod.get("podId") or pod.get("podID")
        if not pod_id:
            raise RuntimeError(f"Could not determine pod id from: {json.dumps(pod)}")

        # Start (if stopped)
        status = (pod.get("runtime", {}) or {}).get("state") or pod.get("status")
        if status in ("STOPPED", "STOPPING", "PAUSED"):
            eprint(f"[launch_pod] Pod is {status}; starting…")
            start_pod(pod_id)

        # Set idle timeout if requested
        if args.idle_minutes and args.idle_minutes > 0:
            update_pod_autostop(pod_id, args.idle_minutes)

        # Wait until ready
        if args.wait_ready and args.wait_ready > 0:
            pod = wait_until_ready(pod_id, args.wait_ready)

        # Optionally print endpoints
        check_ports_flag = str(args.check_ports).strip().lower() in ("1", "true", "yes", "y")
        if check_ports_flag:
            check_http_tcp(pod)

        # Emit POD_ID=... (the workflow step will scrape this into GITHUB_OUTPUT)
        print(f"POD_ID={pod_id}")

    except Exception as ex:
        eprint(f"[launch_pod] FATAL: {ex}")
        sys.exit(1)
