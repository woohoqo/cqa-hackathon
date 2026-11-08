"""Run the opt-in shared demo. Credentials stay local; existing .env is never modified."""
import argparse
import json
import os
from pathlib import Path
import secrets

import uvicorn

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--public", action="store_true", help="Allow demo visitors without a password")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    os.environ["CQA_DEMO_PUBLIC"] = "1" if args.public else "0"
    if args.public:
        os.environ.pop("CQA_DEMO_PASSWORD", None)
        print("Public demo enabled: visitors do not need a password.", flush=True)
    else:
        credentials = ROOT / "private" / "demo-access.json"
        credentials.parent.mkdir(parents=True, exist_ok=True)
        try:
            with credentials.open("x", encoding="utf-8") as target:
                json.dump({"username": "demo", "password": secrets.token_urlsafe(24)}, target)
        except FileExistsError:
            pass
        saved = json.loads(credentials.read_text(encoding="utf-8"))
        if saved.get("username") != "demo" or not isinstance(saved.get("password"), str) or len(saved["password"]) < 20:
            raise RuntimeError("Invalid local demo credentials; existing file was not changed")
        os.environ["CQA_DEMO_PASSWORD"] = saved["password"]
        print("Demo credentials are in private/demo-access.json (not printed).", flush=True)
    os.chdir(ROOT)
    uvicorn.run("backend.app.demo:create_demo_app", factory=True, host="127.0.0.1", port=args.port,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1", access_log=False)


if __name__ == "__main__":
    main()
