"""Check a deployed Consultant Experience instance is actually wired up correctly.

Run it against the Render URL as soon as the first deploy finishes:

    python -m scripts.verify_deployment https://consultant-experience.onrender.com

It catches the failures that look fine from the browser:
  * running on SQLite because DATABASE_URL never reached the app (data will be
    lost on the next deploy)
  * STORAGE_BACKEND left on `local`, so uploaded decks vanish on redeploy
  * ADMIN_TOKEN still the default, leaving uploads open to anyone
  * the knowledge base open to the internet with no APP_PASSWORD

Add --admin-token to also exercise the authenticated paths, and --password if
the site is behind APP_PASSWORD.
"""
from __future__ import annotations

import argparse
import base64
import json
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 60  # a cold Render free instance can take ~50s to wake


class Check:
    def __init__(self) -> None:
        self.failed = 0
        self.warned = 0

    def ok(self, label: str, detail: str = "") -> None:
        print(f"  PASS  {label}" + (f" - {detail}" if detail else ""))

    def warn(self, label: str, detail: str) -> None:
        self.warned += 1
        print(f"  WARN  {label} - {detail}")

    def fail(self, label: str, detail: str) -> None:
        self.failed += 1
        print(f"  FAIL  {label} - {detail}")


def request(url: str, *, token: str = "", password: str = "", method: str = "GET"):
    req = urllib.request.Request(url, method=method)
    if token:
        req.add_header("X-Admin-Token", token)
    if password:
        raw = base64.b64encode(f"x:{password}".encode()).decode()
        req.add_header("Authorization", f"Basic {raw}")
    return urllib.request.urlopen(req, timeout=TIMEOUT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a deployed instance.")
    parser.add_argument("url", help="Base URL, e.g. https://consultant-experience.onrender.com")
    parser.add_argument("--admin-token", default="", help="Exercise authenticated endpoints too.")
    parser.add_argument("--password", default="", help="APP_PASSWORD, if the site is gated.")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    check = Check()
    print(f"\nVerifying {base}\n")

    # --- reachable and healthy -------------------------------------------
    print("Connectivity")
    try:
        with request(f"{base}/healthz", password=args.password) as response:
            health = json.loads(response.read())
        check.ok("health endpoint", f"database={health.get('database')}")
    except urllib.error.HTTPError as exc:
        check.fail("health endpoint", f"HTTP {exc.code} (a cold free instance can take ~50s - retry)")
        return 1
    except Exception as exc:
        check.fail("health endpoint", f"unreachable: {exc}")
        return 1

    # --- configuration ----------------------------------------------------
    print("\nConfiguration")
    try:
        with request(f"{base}/api/status", password=args.password) as response:
            status = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            check.fail("status", "401 - the site is password protected; pass --password")
        else:
            check.fail("status", f"HTTP {exc.code}")
        return 1

    if status["database"] == "postgres":
        check.ok("database", "Postgres - data survives redeploys")
    else:
        check.fail("database", "SQLite! DATABASE_URL did not reach the app; "
                               "the knowledge base will be WIPED on the next deploy")

    backend = status["storage_backend"]
    if backend in ("supabase", "s3"):
        check.ok("file storage", f"{backend} - uploaded decks survive redeploys")
    elif backend == "none":
        check.warn("file storage", "originals are not retained (STORAGE_BACKEND=none)")
    else:
        check.fail("file storage", f"{backend}! uploaded decks will be LOST on the next deploy")

    if status["admin_token_is_default"]:
        check.fail("admin token", "still the default value - anyone can upload or delete")
    else:
        check.ok("admin token", "changed from the default")

    if status["llm_configured"]:
        check.ok("LLM key", f"set, model={status['model']}")
    else:
        check.fail("LLM key", "not set - chat and interview practice will 503")

    if status["password_protected"]:
        check.ok("site password", "APP_PASSWORD is set")
    else:
        check.warn("site password", "not set - anyone with the URL can read the knowledge base")

    # --- the knowledge base actually works --------------------------------
    print("\nKnowledge base")
    check.ok("documents indexed", f"{status['documents']} documents, {status['chunks']} chunks")
    if status["missing_embeddings"]:
        check.warn("embeddings", f"{status['missing_embeddings']} chunks have no current vector - run Re-index")
    else:
        check.ok("embeddings", f"{status['embedded_chunks']} chunks embedded "
                               f"({status['embedding_provider']}, {status['embedding_dim']}d)")

    query = urllib.parse.urlencode({"q": "what causes a settlement fail", "top_k": 3})
    try:
        with request(f"{base}/api/documents/search/query?{query}", password=args.password) as response:
            hits = json.loads(response.read())
        if hits:
            check.ok("retrieval", f"{len(hits)} hits, top: {hits[0]['document_title'][:40]!r}")
        else:
            check.warn("retrieval", "no hits - is the knowledge base empty?")
    except Exception as exc:
        check.fail("retrieval", str(exc))

    # --- the UI is served -------------------------------------------------
    print("\nWeb UI")
    for path in ("/", "/static/app.js", "/static/styles.css"):
        try:
            with request(f"{base}{path}", password=args.password) as response:
                check.ok(f"serves {path}", f"{response.status}, {len(response.read())} bytes")
        except Exception as exc:
            check.fail(f"serves {path}", str(exc))

    # --- authenticated paths ---------------------------------------------
    if args.admin_token:
        print("\nAdmin access")
        try:
            with request(f"{base}/api/documents", token=args.admin_token, password=args.password) as response:
                documents = json.loads(response.read())
            retained = [d for d in documents if d.get("object_key")]
            check.ok("admin token accepted", f"{len(documents)} documents, {len(retained)} with a stored original")
        except urllib.error.HTTPError as exc:
            check.fail("admin token", f"HTTP {exc.code}")

    # --- verdict ----------------------------------------------------------
    print()
    if check.failed:
        print(f"{check.failed} FAILED, {check.warned} warning(s). Fix the failures before uploading real material.")
        return 1
    print(f"All checks passed ({check.warned} warning(s)). The deployment is sound.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
