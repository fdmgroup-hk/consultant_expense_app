"""Create (or update) the Render web service from this repo, via Render's API.

The Render dashboard offers several service types and only one of them is right
for this app - it is a FastAPI server in a Docker container, so it needs a Web
Service, not a Static Site. Doing it through the API removes that choice, and
sets all fourteen environment variables in one go.

    # key from Render > Account Settings > API Keys, saved to render_key.txt
    python -m scripts.deploy_render

    python -m scripts.deploy_render --dry-run     # show what would be sent
    python -m scripts.deploy_render --wait        # follow the build to completion

Idempotent: if a service of the same name already exists it updates that one's
environment variables and triggers a redeploy rather than creating a duplicate.
Secrets are read from render_key.txt and .env - never passed on the command line,
where they would land in shell history.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
API = "https://api.render.com/v1"
SERVICE_NAME = "consultant-experience"
REPO = "https://github.com/fdmgroup-hk/consultant_expense_app"
BRANCH = "main"
REGION = "singapore"


def load_key() -> str:
    key = os.environ.get("RENDER_API_KEY", "").strip()
    if not key:
        path = ROOT / "render_key.txt"
        if not path.is_file():
            raise SystemExit(
                "No Render API key. Create one at Render > Account Settings > API Keys "
                f"and save it to {path}"
            )
        key = path.read_text(encoding="utf-8").strip()
    if not key:
        raise SystemExit("render_key.txt is empty.")
    return key


def env_from_dotenv() -> dict[str, str]:
    """Read deployment values.

    .env.render wins over .env: the local .env deliberately has no
    DATABASE_URL so local runs stay on SQLite, but the deployed service needs
    the Supabase one.
    """
    values: dict[str, str] = {}
    for name in (".env", ".env.render"):
        path = ROOT / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if v.strip():
                    values[k.strip()] = v.strip()
    return values


def build_env_vars() -> list[dict[str, str]]:
    local = env_from_dotenv()
    scratch = pathlib.Path(
        os.environ.get("CE_SCRATCH", "")
    )  # optional: dburl.txt / service_key.txt live here during setup

    def pick(key: str, fallback: str = "") -> str:
        return local.get(key) or fallback

    database_url = pick("DATABASE_URL")
    service_key = pick("SUPABASE_SERVICE_KEY")
    if scratch.is_dir():
        if not database_url and (scratch / "dburl.txt").is_file():
            database_url = (scratch / "dburl.txt").read_text(encoding="utf-8").strip()
        if not service_key and (scratch / "service_key.txt").is_file():
            service_key = (scratch / "service_key.txt").read_text(encoding="utf-8").strip()

    required = {
        "GOOGLE_API_KEY": pick("GOOGLE_API_KEY"),
        "DATABASE_URL": database_url,
        "SUPABASE_SERVICE_KEY": service_key,
        "ADMIN_TOKEN": pick("ADMIN_TOKEN"),
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        raise SystemExit(
            "These values are needed but not found in .env: " + ", ".join(missing)
        )

    env_vars = {
        **required,
        "LLM_PROVIDER": "gemini",
        "GEMINI_MODEL": pick("GEMINI_MODEL", "gemini-3.6-flash"),
        "STORAGE_BACKEND": "supabase",
        "SUPABASE_URL": pick("SUPABASE_URL", "https://pxjiljyswcgcryapffir.supabase.co"),
        "SUPABASE_BUCKET": pick("SUPABASE_BUCKET", "consultant-originals"),
        "EMBEDDING_PROVIDER": "auto",
        "PYTHONUNBUFFERED": "1",
    }
    password = pick("APP_PASSWORD")
    if password:
        env_vars["APP_PASSWORD"] = password
    return [{"key": k, "value": v} for k, v in env_vars.items()]


def call(key: str, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{API}{path}", data=data, method=method)
    request.add_header("Authorization", f"Bearer {key}")
    request.add_header("Accept", "application/json")
    if data:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            raw = response.read().decode()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw[:600]}


def redact(env_vars: list[dict[str, str]]) -> list[dict[str, str]]:
    secret = {"GOOGLE_API_KEY", "DATABASE_URL", "SUPABASE_SERVICE_KEY", "ADMIN_TOKEN", "APP_PASSWORD"}
    return [
        {"key": e["key"], "value": ("<set, %d chars>" % len(e["value"])) if e["key"] in secret else e["value"]}
        for e in env_vars
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy this repo to Render.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--wait", action="store_true", help="Follow the build until it finishes.")
    parser.add_argument("--name", default=SERVICE_NAME)
    args = parser.parse_args()

    # A dry run should work before the API key exists.
    key = "" if args.dry_run else load_key()
    env_vars = build_env_vars()

    print(f"Service : {args.name}")
    print(f"Repo    : {REPO} @ {BRANCH}")
    print(f"Region  : {REGION}   plan: free   runtime: docker")
    print("Env vars:")
    for e in redact(env_vars):
        print(f"  {e['key']:22} {e['value']}")

    if args.dry_run:
        print("\nDry run - nothing sent.")
        return 0

    status, owners = call(key, "GET", "/owners?limit=20")
    if status != 200:
        raise SystemExit(f"Could not list owners (HTTP {status}): {owners}")
    owner = owners[0]["owner"]
    print(f"\nOwner   : {owner['name']} ({owner['id']})")

    status, existing = call(key, "GET", f"/services?name={args.name}&limit=20")
    match = None
    if status == 200 and existing:
        match = next((s["service"] for s in existing if s["service"]["name"] == args.name), None)

    if match:
        service_id = match["id"]
        print(f"Existing service found ({service_id}) - updating env vars and redeploying.")
        status, body = call(key, "PUT", f"/services/{service_id}/env-vars", env_vars)
        if status not in (200, 201):
            raise SystemExit(f"Setting env vars failed (HTTP {status}): {body}")
        status, body = call(key, "POST", f"/services/{service_id}/deploys", {"clearCache": "do_not_clear"})
        if status not in (200, 201, 202):
            raise SystemExit(f"Triggering deploy failed (HTTP {status}): {body}")
        deploy_id = body.get("id")
    else:
        payload = {
            "type": "web_service",
            "name": args.name,
            "ownerId": owner["id"],
            "repo": REPO,
            "branch": BRANCH,
            "autoDeploy": "yes",
            "envVars": env_vars,
            "serviceDetails": {
                "env": "docker",
                "region": REGION,
                "plan": "free",
                "healthCheckPath": "/healthz",
                "envSpecificDetails": {"dockerfilePath": "./Dockerfile", "dockerContext": "."},
            },
        }
        status, body = call(key, "POST", "/services", payload)
        if status not in (200, 201):
            raise SystemExit(f"Creating the service failed (HTTP {status}):\n{json.dumps(body, indent=2)[:1200]}")
        service = body.get("service", body)
        service_id = service["id"]
        deploy_id = (body.get("deployId") or "")
        print(f"Created service {service_id}")

    status, service = call(key, "GET", f"/services/{service_id}")
    url = (service or {}).get("serviceDetails", {}).get("url", "")
    print(f"\nURL     : {url or '(assigned once the first build completes)'}")
    print(f"Dashboard: https://dashboard.render.com/web/{service_id}")

    if args.wait:
        print("\nFollowing the build (first Docker build takes 5-10 minutes)...")
        seen = None
        deadline = time.time() + 20 * 60
        while time.time() < deadline:
            status, deploys = call(key, "GET", f"/services/{service_id}/deploys?limit=1")
            if status == 200 and deploys:
                deploy = deploys[0]["deploy"]
                state = deploy.get("status")
                if state != seen:
                    print(f"  {time.strftime('%H:%M:%S')}  {state}")
                    seen = state
                if state in ("live", "build_failed", "update_failed", "canceled", "deactivated"):
                    if state == "live":
                        print(f"\nLIVE at {url}")
                        return 0
                    print(f"\nDeploy ended as '{state}'. Logs: https://dashboard.render.com/web/{service_id}/logs")
                    return 1
            time.sleep(15)
        print("\nStill building after 20 minutes - check the dashboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
