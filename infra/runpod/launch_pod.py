import os, json, argparse, requests
from dotenv import load_dotenv

load_dotenv()
API_KEY = os.getenv("RUNPOD_API_KEY")
BASE = "https://rest.runpod.io/v1"

# Any env here will be merged into pod_config["env"] if present in the runner's env
SECRET_ENV_KEYS = [
    "HUGGINGFACE_TOKEN",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "DISCORD_APP_ID",
    "DISCORD_PUBLIC_KEY",
    "TWITTER_API_KEY",
    "TWITTER_API_KEY_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_TOKEN_SECRET",
    "TWITTER_BEARER_TOKEN",
    "GITHUB_TOKEN",
    "DOCKER_ACCESS_TOKEN",
    "GIT_PAT",
]

def create_pod(body):
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    r = requests.post(f"{BASE}/pods", headers=headers, json=body, timeout=60)
    r.raise_for_status()
    return r.json()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default=None)
    ap.add_argument("--config", default="infra/runpod/pod_config.json")
    args = ap.parse_args()

    if not API_KEY:
        raise SystemExit("Missing RUNPOD_API_KEY")

    with open(args.config, "r", encoding="utf-8-sig") as f:
        body = json.load(f)

    if args.name:
        body["name"] = args.name

    # Merge secrets from env into config
    body.setdefault("env", {})
    for k in SECRET_ENV_KEYS:
        v = os.getenv(k)
        if v:
            body["env"][k] = v

    # Print a safe preview (mask env values)
    safe = dict(body)
    if "env" in safe:
        safe["env"] = {k: "***" for k in safe["env"].keys()}
    print("[launch] Final pod body (masked):")
    print(json.dumps(safe, indent=2))

    resp = create_pod(body)
    print(json.dumps(resp, indent=2))

if __name__ == "__main__":
    main()
