"""Load the starter knowledge pack in ``seed/``.

The app is useful on first run, before anyone has uploaded a deck: the pack
covers the trade lifecycle, order flow and the three role profiles. Real
consultant material uploaded later sits alongside it and outranks it whenever it
is more specific.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .. import db
from ..config import PROJECT_ROOT
from .pipeline import DocumentMeta, DuplicateDocument, ingest_file

logger = logging.getLogger(__name__)

SEED_DIR = PROJECT_ROOT / "seed"

# filename stem -> (title, role)
SEED_META = {
    "trade-lifecycle": ("Trade Lifecycle - end to end", "general"),
    "order-flow-and-fix": ("Order Flow, Venues and FIX", "general"),
    "role-developer": ("Developer placement - what the job is", "developer"),
    "role-production-support": ("Production Support placement - what the job is", "production_support"),
    "role-business-analyst": ("Business Analyst placement - what the job is", "business_analyst"),
    "interview-questions": ("Interview question bank and model answers", "general"),
    "glossary": ("Capital markets glossary for new consultants", "general"),
}


def knowledge_base_is_empty() -> bool:
    with db.connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM documents").fetchone()
        return int(row["n"]) == 0


def load_seed_pack(force: bool = False) -> list[dict]:
    """Ingest every markdown file in ``seed/``. Returns what was indexed."""
    if not SEED_DIR.exists():
        logger.warning("No seed directory at %s", SEED_DIR)
        return []

    results: list[dict] = []
    for path in sorted(SEED_DIR.glob("*.md")):
        title, role = SEED_META.get(
            path.stem, (path.stem.replace("-", " ").title(), "general")
        )
        meta = DocumentMeta(
            title=title,
            role=role,
            client=None,
            consultant=None,
            tags=["starter-pack"],
            notes="Shipped with the app. Replace or supplement with real consultant material.",
        )
        try:
            results.append(ingest_file(path, meta, replace_existing=force))
        except DuplicateDocument:
            logger.debug("Seed file already indexed: %s", path.name)
        except Exception:
            logger.exception("Could not index seed file %s", path.name)
    if results:
        logger.info("Loaded %d starter knowledge documents.", len(results))
    return results
