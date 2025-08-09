import os, json, argparse, requests
from dotenv import load_dotenv
load_dotenv()
API_KEY = os.getenv("RUNPOD_API_KEY")
BASE = "https://rest.runpod.io/v1"
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
    with open(args.config, "r") as f:
        body = json.load(f)
    if args.name:
        body["name"] = args.name
    print(json.dumps(create_pod(body), indent=2))
if __name__ == "__main__":
    main()
