#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Upsert a RunPod pod by name:
- If a pod with the given name exists -> PATCH it with the config.
- If it doesn't exist -> POST to create it.
- Prints a GitHub Actions notice like: ::notice title=RunPod::POD_ID=...

Expects:
  - ENV: RUNPOD_API_KEY
  - Config JSON: infra/runpod/pod_config.json (default), or pass --config

You can override the name with --name.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from typing import Any, Dict, Optional

import requests


BASE_URL = "https://api.runpod.io/v2"
CONFIG_PATH_DEFAULT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "runpod",
    "pod_config.json",
)
TIMEOUT = 30


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def api(
    method: str,
    path: str,
    token: str,
    json_body: Optional[Dict[str, Any]] = None,
    timeout: int = TIMEOUT,
) -> requests.Response:
    """
    Simple API wrapper that raises for HTTP >=400.
    NOTE: Use raw requests.* in places where we want to ignore 404s.
    """
    url = f"{BASE_URL.rstrip('/')}/{path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    r = requests.request(method.upper(), url, headers=headers, json=json_body, timeout=timeout)
    if r.status_code >= 400:
        # Print a compact error context to stderr to aid debugging in CI logs.
        snippet = None
        try:
            snippet = r.json()
        except Exception:
            snippet = r.text
        eprint(f"[launch_pod] ERROR: {method.upper()} {path} -> {r.status_code}: {snippet}")
        r.raise_for_status()
    return r


def load_pod_config(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read().strip()
    if not text:
        raise RuntimeError(f"Config file is empty: {path}")
    try:
        data = json.loads(text)
    except Exception as e:
        eprint(f"[launch_pod] ERROR: Invalid JSON in {path}: {e}")
        raise RuntimeError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError(f"Config root must be an object: {path}")
    return data


def redact_env_in_body(body: Dict[str, Any]) -> Dict[str, Any]:
    """
    Return a shallow copy with env values masked for logging.
    """
    copy = dict(body)
    env = copy.get("env")
    if isinstance(env, dict):
        red = {}
        for k, v in env.items():
            sv = str(v)
            if not sv:
                red[k] = sv
                continue
            if len(sv) <= 8:
                red[k] = "****"
            else:
                red[k] = sv[:2] + "****" + sv[-2:]
        copy["env"] = red
    return copy


def print_truncated_request(title: str, body: Dict[str, Any], limit: int = 2000) -> None:
    red = redact_env_in_body(body)
    pretty = json.dumps(red, indent=2, ensure_ascii=False)
    out = pretty if len(pretty) <= limit else (pretty[:limit] + "\n... (truncated)")
    print(f"{title}\n{out}")


def find_pod_by_name(name: str, token: str) -> Optional[Dict[str, Any]]:
    """
    Try a fast filtered query first; if the endpoint isn't available (404) or
    doesn't return what we expect, fall back to listing pods and filtering locally.
    """
    # 1) Fast path – tolerate 404 or parse issues and just fall through.
    try:
        r = requests.get(
            f"{BASE_URL}/pods?name={name}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        if r.status_code < 400:
            try:
                j = r.json()
            except Exception:
                j = None
            items = None
            if isinstance(j, dict):
                # Some APIs use { data: [...] } or { pods: [...] }
                items = j.get("data")
                if items is None and "pods" in j:
                    items = j["pods"]
            elif isinstance(j, list):
                items = j

            if isinstance(items, list):
                for p in items:
                    if (p.get("name") or "").strip() == name:
                        return p
    except Exception:
        # Network/JSON errors: ignore and try slow path below.
        pass

    # 2) Slow path – list all and filter locally.
    r2 = api("GET", "/pods", token)
    try:
        j2 = r2.json()
    except Exception:
        return None

    items = None
    if isinstance(j2, dict):
        items = j2.get("data")
        if items is None and "pods" in j2:
            items = j2["pods"]
    elif isinstance(j2, list):
        items = j2

    if isinstance(items, list):
        for p in items:
            if (p.get("name") or "").strip() == name:
                return p
    return None


def upsert_pod(config: Dict[str, Any], token: str) -> Dict[str, Any]:
    """
    Upsert by name:
      - Find by name
      - PATCH if found
      - POST if not found
    Returns the final pod object (as returned by the API).
    """
    name = (config.get("name") or "").strip()
    if not name:
        raise RuntimeError("Config must include a non-empty 'name'.")

    pod = find_pod_by_name(name, token)

    if pod and "id" in pod:
        pod_id = pod["id"]
        # Prepare patch body; you can send the same config or selectively update fields.
        # Here we send the whole config so your updates (env/ports/etc.) apply.
        print_truncated_request("Final pod request body (truncated to 2000 chars):", config)
        r = api("PATCH", f"/pods/{pod_id}", token, json_body=config)
        try:
            return r.json()
        except Exception:
            return {"id": pod_id, "name": name, "status": "patched"}
    else:
        # Create new pod
        print_truncated_request("Final pod request body (truncated to 2000 chars):", config)
        r = api("POST", "/pods", token, json_body=config)
        try:
            return r.json()
        except Exception:
            # Best effort fallback; the create call should return the pod object with id
            return {"name": name, "status": "created"}


def extract_pod_id(pod_obj: Any) -> Optional[str]:
    """
    The API may wrap payloads. Try a few common shapes to get the ID.
    """
    if not isinstance(pod_obj, (dict,)):
        return None
    # direct
    if "id" in pod_obj and isinstance(pod_obj["id"], str):
        return pod_obj["id"]
    # data wrapper
    data = pod_obj.get("data")
    if isinstance(data, dict) and isinstance(data.get("id"), str):
        return data["id"]
    # nested pod
    pod = pod_obj.get("pod")
    if isinstance(pod, dict) and isinstance(pod.get("id"), str):
        return pod["id"]
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create or update a RunPod pod using infra/runpod/pod_config.json"
    )
    parser.add_argument(
        "--config",
        default=CONFIG_PATH_DEFAULT,
        help=f"Path to pod config JSON (default: {CONFIG_PATH_DEFAULT})",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Override the pod name from config JSON",
    )
    args = parser.parse_args()

    token = os.getenv("RUNPOD_API_KEY", "").strip()
    if not token:
        eprint("RUNPOD_API_KEY is required in environment.")
        return 2

    # Load config
    cfg = load_pod_config(args.config)

    # Optional override of name
    if args.name:
        cfg["name"] = args.name

    # A little debugging block like in your workflow step:
    print("##[group]Run set -x")
    print("set -x")
    print("ls -la infra/runpod || true")
    print("wc -c infra/runpod/pod_config.json || true")
    print("sed -n '1,120p' infra/runpod/pod_config.json || true")
    print("##[endgroup]")

    # Upsert
    result = upsert_pod(cfg, token)

    # Pull out an ID for GitHub Actions log consumption
    pod_id = extract_pod_id(result)
    name = cfg.get("name")

    # Emit a helpful GitHub Actions notice (consumable by later steps)
    # Format: ::notice title=RunPod::POD_ID=...,NAME=...,REGION=...
    region = cfg.get("regionId") or cfg.get("region") or ""
    title = "RunPod"
    fields = [
        f"POD_ID={pod_id or ''}",
        f"NAME={name or ''}",
        f"REGION={region}",
    ]
    print(f"::notice title={title}::{','.join(fields)}")

    # Also print a concise human-readable summary
    summary_lines = [
        "RunPod upsert complete:",
        f"  Name:   {name}",
        f"  Region: {region}",
        f"  PodID:  {pod_id or '(unknown from response)'}",
    ]
    print("\n".join(summary_lines))

    return 0


if __name__ == "__main__":
    sys.exit(main())
