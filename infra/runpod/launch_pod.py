#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Launch or update a RunPod pod (best-effort upsert by name), optionally wait
until it's running, optionally check exposed ports, and emit POD_ID for workflows.

This script is defensive: RunPod's v2 REST surface may differ by account/region.
We try name-lookup first; if the API returns 404 for list/search endpoints,
we fall back to creation. If creation conflicts (name already exists), we try
to discover the pod ID via other endpoints before giving up.

Environment:
  RUNPOD_API_KEY      (required)
  RUNPOD_API_BASE     (optional, default: https://api.runpod.io)
  GITHUB_OUTPUT       (GitHub Actions sets this; used to emit POD_ID)

CLI:
  --name NAME
  --wait-ready SECONDS         (default: 0 -> don't wait)
  --check-ports true|false     (default: false)
  --idle-minutes MINUTES       (default: 0 -> disabled)

Config file:
  infra/runpod/pod_config.json  (base pod spec; we inject/override .name and may
                                append env vars / hints like IDLE_MINUTES)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import socket
from typing import Any, Dict, Optional

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CONFIG_PATH = os.path.join(HERE, "pod_config.json")

DEFAULT_API_BASE = os.environ.get("RUNPOD_API_BASE", "https://api.runpod.io").rstrip("/")
API_TIMEOUT = 30
SESSION = requests.Session()


def log(msg: str) -> None:
    print(msg, flush=True)


def debug(errprefix: str, e: requests.RequestException, resp: Optional[requests.Response]) -> None:
    try:
        body = resp.json() if resp is not None else None
    except Exception:
        body = resp.text if resp is not None else None
    log(f"[launch_pod] {errprefix}: {str(e)}; response={body}")


def api(method: str, path: str, token: str, **kwargs) -> requests.Response:
    url = f"{DEFAULT_API_BASE}{path}"
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    headers["Content-Type"] = "application/json"
    # Don't let requests print secrets on error
    kwargs.setdefault("timeout", API_TIMEOUT)
    return SESSION.request(method, url, headers=headers, **kwargs)


def read_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        # Guard against BOM
        raw = f.read().lstrip("\ufeff")
        return json.loads(raw)


def merge_env(cfg_env: Dict[str, str], extras: Dict[str, str]) -> Dict[str, str]:
    out = dict(cfg_env or {})
    for k, v in extras.items():
        if v is None or v == "":
            continue
        out[k] = v
    return out


def find_pod_by_name(name: str, token: str) -> Optional[Dict[str, Any]]:
    """
    Best-effort search by name.
    Different accounts may not have a 'GET /v2/pods' or '/v2/pods?name=…'.
    We try a couple of patterns; any 404s are tolerated.
    """
    try_paths = [
        f"/v2/pods?name={name}",
        "/v2/pods",  # if supported, filter client-side
    ]
    for p in try_paths:
        try:
            r = api("GET", p, token)
            if r.status_code == 404:
                # Endpoint not available in this account/plan — try next
                log(f"[launch_pod] NOTE: {p} returned 404; trying alternate lookup.")
                continue
            r.raise_for_status()
            data = r.json()
            # data might be a list or an object with 'data' key depending on API surface
            pods = []
            if isinstance(data, list):
                pods = data
            elif isinstance(data, dict):
                if "data" in data and isinstance(data["data"], list):
                    pods = data["data"]
                elif "pods" in data and isinstance(data["pods"], list):
                    pods = data["pods"]
            for pod in pods:
                if str(pod.get("name", "")).strip().lower() == name.strip().lower():
                    return pod
        except requests.RequestException as e:
            debug(f"ERROR: GET {p}", e, resp=getattr(e, "response", None))
            # Continue trying alternatives
            continue
    return None


def create_pod(spec: Dict[str, Any], token: str) -> Dict[str, Any]:
    r = api("POST", "/v2/pods", token, json=spec)
    if r.status_code == 404:
        # Some tenants expose creation at /v2/pods/create
        r = api("POST", "/v2/pods/create", token, json=spec)
    r.raise_for_status()
    return r.json()


def get_pod(pod_id: str, token: str) -> Dict[str, Any]:
    r = api("GET", f"/v2/pods/{pod_id}", token)
    r.raise_for_status()
    return r.json()


def update_pod(pod_id: str, spec: Dict[str, Any], token: str) -> Dict[str, Any]:
    # Some surfaces use PATCH, others PUT — try PATCH then PUT.
    r = api("PATCH", f"/v2/pods/{pod_id}", token, json=spec)
    if r.status_code in (404, 405):
        r = api("PUT", f"/v2/pods/{pod_id}", token, json=spec)
    r.raise_for_status()
    return r.json()


def wait_until_running(pod_id: str, token: str, timeout_secs: int) -> Dict[str, Any]:
    if timeout_secs <= 0:
        return get_pod(pod_id, token)
    deadline = time.time() + timeout_secs
    last_state = ""
    while time.time() < deadline:
        pod = get_pod(pod_id, token)
        # Common shapes: { status: { phase: "RUNNING" } } OR { state: "RUNNING" }
        phase = (
            pod.get("status", {}).get("phase")
            or pod.get("status")
            or pod.get("state")
            or ""
        )
        phase_str = str(phase).upper()
        if phase_str != last_state:
            log(f"[launch_pod] pod {pod_id} state: {phase_str}")
            last_state = phase_str
        if phase_str in ("RUNNING", "READY", "HEALTHY"):
            return pod
        time.sleep(5)
    raise TimeoutError(f"Timed out waiting for pod {pod_id} to become RUNNING after {timeout_secs}s")


def tcp_check(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def discover_host_and_ports(pod: Dict[str, Any]) -> tuple[Optional[str], list[int]]:
    """
    Do best-effort discovery. Depending on account, the public address may be under:
      pod["publicIp"], pod["status"]["network"]["publicIp"], pod["proxy"]["host"], etc.
    Ports list can come from config "ports" (comma string) — we fall back to that.
    """
    host_candidates = [
        pod.get("publicIp"),
        pod.get("public_ip"),
        pod.get("proxy", {}).get("host") if isinstance(pod.get("proxy"), dict) else None,
        pod.get("status", {}).get("network", {}).get("publicIp")
        if isinstance(pod.get("status"), dict)
        else None,
    ]
    host = next((h for h in host_candidates if h), None)

    ports: list[int] = []
    # Try to find ports array in pod detail
    if "ports" in pod and isinstance(pod["ports"], list):
        try:
            ports = [int(p) for p in pod["ports"]]
        except Exception:
            pass
    return host, ports


def upsert_pod(cfg: Dict[str, Any], token: str, name: str) -> Dict[str, Any]:
    """
    Upsert strategy:
      1) Try to find by name; if found, send update (PATCH/PUT) with new spec.
      2) If not found or listing not supported, try create.
      3) If create conflicts because name exists, attempt another lookup path.
    """
    # Ensure name in spec
    spec = dict(cfg)
    spec["name"] = name

    # Inject IDLE_MINUTES as an env to your container (your entrypoint can implement it)
    env = spec.get("env", {})
    env = merge_env(env, {"IDLE_MINUTES": os.environ.get("IDLE_MINUTES", "")})
    spec["env"] = env

    found = find_pod_by_name(name, token)
    if found and "id" in found:
        pod_id = str(found["id"])
        log(f"[launch_pod] Updating existing pod '{name}' (id={pod_id})")
        updated = update_pod(pod_id, spec, token)
        updated["id"] = updated.get("id", pod_id)  # keep id
        return updated

    log(f"[launch_pod] Creating pod '{name}' (no existing match found)")
    try:
        created = create_pod(spec, token)
        # Some responses wrap the pod; try to normalize
        pod_obj = created.get("pod") if isinstance(created, dict) else None
        if isinstance(pod_obj, dict) and "id" in pod_obj:
            return pod_obj
        return created
    except requests.HTTPError as e:
        resp = getattr(e, "response", None)
        status = resp.status_code if resp is not None else None
        text = ""
        try:
            text = resp.text if resp is not None else ""
        except Exception:
            pass
        if status == 409 or ("exists" in text.lower() if text else False):
            log(f"[launch_pod] Create reported conflict; trying to re-discover existing pod by name.")
            existing = find_pod_by_name(name, token)
            if existing:
                return existing
        debug("ERROR: create pod", e, resp)
        raise


def write_github_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        # Fallback to print if not in Actions
        log(f"{key}={value}")
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def parse_bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True, help="Pod name")
    parser.add_argument("--wait-ready", default="0", help="Seconds to wait until running")
    parser.add_argument("--check-ports", default="false", help="true/false")
    parser.add_argument("--idle-minutes", default="0", help="Auto-stop if idle (inform container via env)")
    args = parser.parse_args()

    token = os.environ.get("RUNPOD_API_KEY", "").strip()
    if not token:
        log("ERROR: RUNPOD_API_KEY env var is required")
        return 1

    # Expose idle minutes to env so spec merge picks it up
    os.environ["IDLE_MINUTES"] = str(args.idle_minutes)

    # Load base spec
    try:
        cfg = read_config()
    except Exception as e:
        log(f"ERROR reading config {CONFIG_PATH}: {e}")
        return 1

    # Upsert
    pod = upsert_pod(cfg, token, name=args.name)
    pod_id = str(pod.get("id") or pod.get("_id") or "")
    if not pod_id:
        log(f"[launch_pod] WARNING: Could not determine pod id from response: {json.dumps(pod)[:500]}")
    else:
        log(f"[launch_pod] POD_ID = {pod_id}")
        write_github_output("POD_ID", pod_id)

    # Wait for RUNNING if requested
    wait_secs = int(str(args.wait_ready).strip() or "0")
    if pod_id and wait_secs > 0:
        try:
            pod = wait_until_running(pod_id, token, wait_secs)
        except Exception as e:
            log(f"[launch_pod] WARNING: wait_until_running failed: {e}")

    # Port checks (best effort)
    if parse_bool(args.check_ports):
        # Re-fetch latest snapshot
        if pod_id:
            try:
                pod = get_pod(pod_id, token)
            except Exception:
                pass

        host, ports = discover_host_and_ports(pod)
        # If ports weren't discoverable from the pod detail, fall back to config file
        if not ports:
            try:
                cfg_ports = cfg.get("ports", "")
                if isinstance(cfg_ports, str):
                    ports = [int(p.strip()) for p in cfg_ports.split(",") if p.strip().isdigit()]
            except Exception:
                ports = []

        if not host:
            log("[launch_pod] NOTE: Could not determine public host/IP; skipping port checks.")
        elif not ports:
            log("[launch_pod] NOTE: No port list found; skipping port checks.")
        else:
            bad = []
            for pnum in ports:
                ok = tcp_check(host, pnum, timeout=2.5)
                log(f"[launch_pod] Port {pnum} on {host}: {'OPEN' if ok else 'CLOSED'}")
                if not ok:
                    bad.append(pnum)
            if bad:
                log(f"[launch_pod] WARNING: some ports appear closed: {bad}")

    # Print final pod summary (truncated)
    try:
        summary = json.dumps(pod, indent=2) if isinstance(pod, dict) else str(pod)
        log(f"[launch_pod] Final pod object (truncated):\n{summary[:2000]}")
    except Exception:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
