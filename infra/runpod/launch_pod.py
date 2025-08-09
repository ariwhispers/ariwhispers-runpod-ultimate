#!/usr/bin/env python3
"""
Robust launcher:
- Loads infra/runpod/pod_config.json relative to this file
- Handles UTF-8 with or without BOM
- Fails loudly if file missing/empty/invalid
- Injects --name override if provided
- (Placeholder) prints final body; integrate your RunPod API call where indicated
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


def load_pod_config() -> Dict[str, Any]:
    cfg_path = Path(__file__).with_name("pod_config.json")
    if not cfg_path.exists():
        raise FileNotFoundError(f"pod_config.json not found at: {cfg_path}")

    # Handle BOM and strip whitespace
    text = cfg_path.read_text(encoding="utf-8-sig").strip()
    if not text:
        raise RuntimeError(f"{cfg_path} is empty after stripping whitespace")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON in {cfg_path}: {e}") from e

    if not isinstance(data, dict):
        raise RuntimeError(f"{cfg_path} must contain a JSON object at top level")

    return data


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Launch or update RunPod pod")
    p.add_argument(
        "--name",
        help="Pod name override (will set/replace the name field in pod_config.json)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    body = load_pod_config()

    # Inject CLI name into the body if provided
    if args.name:
        body["name"] = args.name

    # ---- Integrate your actual RunPod API call here ----
    # Example placeholder: just show what we'd send, then exit non-zero so CI fails
    # if something is clearly wrong before an API call is attempted.
    print("Final pod request body (truncated to 2000 chars):")
    preview = json.dumps(body, indent=2)[:2000]
    print(preview)

    # If you already had working code that POSTs to RunPod,
    # paste it here and use `body` as the payload.
    #
    # Example skeleton:
    # import os, requests
    # api_key = os.environ.get("RUNPOD_API_KEY")
    # if not api_key:
    #     print("RUNPOD_API_KEY is not set", file=sys.stderr)
    #     return 2
    # headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    # resp = requests.post("https://api.runpod.io/some/endpoint", headers=headers, json=body, timeout=60)
    # if resp.status_code >= 300:
    #     print(f"RunPod API error {resp.status_code}: {resp.text}", file=sys.stderr)
    #     return 3
    # print("RunPod API response:", resp.text)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"[launch_pod] ERROR: {e}", file=sys.stderr)
        raise
