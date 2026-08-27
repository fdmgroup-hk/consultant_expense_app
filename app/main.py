"""Consultant Experience - FastAPI application entry point.

Local:      uvicorn app.main:app --reload
Hosted:     the Dockerfile runs uvicorn against $PORT (see DEPLOY.md)
"""
from __future__ import annotations

import base64
import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db, llm
from .config import PROJECT_ROOT, get_settings
from .ingest.seed import knowledge_base_is_empty, load_seed_pack
from .prompts import ROLE_LABELS
from .retrieval import search as retrieval
from .routers import chat, documents, interview
from .schemas import StatusOut
from .security import admin_token_is_default
from .storage import get_storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("consultant_experience")

WEB_DIR = PROJECT_ROOT / "web"

# Paths reachable without the site password, so the platform's health probe and
# the login prompt itself keep working.
_OPEN_PATHS = {"/healthz", "/favicon.ico"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    db.init_db()
    logger.info("Database: %s", "Postgres" if db.is_postgres() else f"SQLite at {settings.db_path}")

    storage = get_storage()
    logger.info("Originals: %s", storage.describe())
    if db.is_postgres() and storage.name == "local":
        logger.warning(
            "STORAGE_BACKEND=local on a hosted deployment - uploaded files will be "
            "lost on the next deploy or restart. Set STORAGE_BACKEND=s3."
        )

    if knowledge_base_is_empty():
        logger.info("Knowledge base is empty - loading the starter pack from seed/")
        load_seed_pack()

    logger.info("LLM provider: %s (%s)", llm.provider(), llm.active_model())
    if not llm.is_configured():
        logger.warning(
            "No API key for provider '%s'. Browsing and search work; chat and "
            "interview practice will return 503 until you add one.", llm.provider()
        )
    if admin_token_is_default():
        logger.warning(
            "ADMIN_TOKEN is still the default value. Change it before this is "
            "reachable from the internet."
        )
    if not settings.app_password:
        logger.info(
            "APP_PASSWORD is not set: anyone with the URL can read and search the "
            "knowledge base. Uploads still require the admin token."
        )
    yield

    db.close_pool()


app = FastAPI(
    title="Consultant Experience",
    description="AI interview preparation grounded in real consultant placement material.",
    version="1.1.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def site_password(request: Request, call_next):
    """Optional whole-site gate via HTTP Basic auth.

    Off unless APP_PASSWORD is set, which keeps the default behaviour open. When
    set, the browser shows its own login prompt - no UI work needed - and the
    gate covers the API and static files alike.
    """
    settings = get_settings()
    if not settings.app_password or request.url.path in _OPEN_PATHS:
        return await call_next(request)

    header = request.headers.get("authorization", "")
    if header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8", "replace")
            supplied = decoded.split(":", 1)[1] if ":" in decoded else ""
        except (ValueError, UnicodeDecodeError):
            supplied = ""
        if hmac.compare_digest(supplied, settings.app_password):
            return await call_next(request)

    return Response(
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Consultant Experience"'},
        content="Authentication required.",
    )


# The UI is same-origin, so this matters only for a separately hosted front end.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        *get_settings().origin_list,
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
app.include_router(chat.router)
app.include_router(interview.router)


@app.get("/api/status", response_model=StatusOut, tags=["status"])
def status() -> StatusOut:
    settings = get_settings()
    with db.connection() as conn:
        doc_count = int(conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()["n"])
        role_rows = conn.execute(
            "SELECT role, COUNT(*) AS n FROM documents GROUP BY role"
        ).fetchall()
        client_rows = conn.execute(
            "SELECT DISTINCT client FROM documents WHERE client IS NOT NULL AND client != '' "
            "ORDER BY client"
        ).fetchall()
        department_rows = conn.execute(
            "SELECT DISTINCT department FROM documents WHERE department IS NOT NULL "
            "AND department != '' ORDER BY department"
        ).fetchall()

    stats = retrieval.index_stats()
    storage = get_storage()
    return StatusOut(
        llm_configured=llm.is_configured(),
        model=llm.active_model(),
        documents=doc_count,
        chunks=stats["chunks"],
        embedded_chunks=stats["embedded_chunks"],
        missing_embeddings=stats["missing_embeddings"],
        embedding_provider=stats["embedding_provider"],
        embedding_model=stats["embedding_model"],
        embedding_dim=stats["embedding_dim"],
        roles={ROLE_LABELS.get(r["role"], r["role"] or "general"): r["n"] for r in role_rows},
        clients=[r["client"] for r in client_rows],
        departments=[r["department"] for r in department_rows],
        admin_token_is_default=admin_token_is_default(),
        database=db.dialect(),
        storage_backend=storage.name,
        retains_originals=storage.retains_originals,
        password_protected=bool(settings.app_password),
    )


@app.get("/healthz", include_in_schema=False)
def healthz() -> JSONResponse:
    """Liveness probe. Touches the database so a dead connection is caught here."""
    try:
        db.healthcheck()
    except Exception as exc:
        logger.exception("Health check failed")
        return JSONResponse(status_code=503, content={"status": "degraded", "error": str(exc)})
    return JSONResponse(content={"status": "ok", "database": db.dialect()})


if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
