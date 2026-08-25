"""Smoke tests — no API key needed.

Everything here exercises storage, extraction, chunking and retrieval, which is
the half of the app that runs without calling Claude. Run with:

    .venv/Scripts/python -m pytest -q          (Windows)
    .venv/bin/python -m pytest -q              (macOS/Linux)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Point the app at a throwaway database before anything imports the settings.
_TMP = tempfile.mkdtemp(prefix="ce_test_")
os.environ["DATA_DIR"] = _TMP
os.environ["EMBEDDING_PROVIDER"] = "hashing"  # deterministic, no downloads
os.environ["ADMIN_TOKEN"] = "test-token"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.ingest.chunk import chunk_segments  # noqa: E402
from app.ingest.extract import Segment  # noqa: E402
from app.ingest.pipeline import DocumentMeta, ingest_file  # noqa: E402
from app.main import app  # noqa: E402
from app.retrieval import search as retrieval  # noqa: E402

SAMPLE = """# Settlement fails

A settlement fail happens when a trade does not settle on the intended settlement date.

## Common causes

- The seller does not have the securities because they were lent out.
- Standard Settlement Instructions were missing or wrong.
- A mismatch in trade details was never resolved before value date.

Under CSDR, cash penalties accrue daily on failing trades in the EU.
"""


@pytest.fixture(scope="module")
def client():
    db.init_db()
    with TestClient(app) as test_client:
        yield test_client


def _write_sample(tmp_path: Path, name: str = "fails.md", body: str = SAMPLE) -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------- chunking

def test_chunker_splits_long_text_and_keeps_locators():
    long_body = "\n\n".join(f"Paragraph {i} about trade settlement mechanics." for i in range(60))
    chunks = chunk_segments([Segment("Slide 1", "Settlement", long_body)], target_chars=400)
    assert len(chunks) > 1
    assert all(c.text.strip() for c in chunks)
    assert all("Slide 1" in c.locator for c in chunks)


def test_chunker_packs_small_neighbouring_slides():
    segments = [Segment(f"Slide {i}", f"Topic {i}", f"Short note number {i}.") for i in range(1, 4)]
    chunks = chunk_segments(segments, target_chars=1000)
    assert len(chunks) == 1
    assert chunks[0].locator == "Slides 1-3"
    assert "Topic 3" in chunks[0].text  # every packed slide's heading survives


def test_chunker_caps_how_many_slides_one_citation_can_span():
    # A citation reading "Slides 1-14" is useless, so packing is capped even when
    # the slides are tiny enough to fit in one chunk by size alone.
    from app.ingest.chunk import MAX_SEGMENTS_PER_CHUNK

    segments = [Segment(f"Slide {i}", "", "tiny.") for i in range(1, 13)]
    chunks = chunk_segments(segments, target_chars=4000)
    assert len(chunks) == 12 / MAX_SEGMENTS_PER_CHUNK
    assert chunks[0].locator == "Slides 1-3"


def test_chunker_keeps_a_meaty_slide_on_its_own():
    meaty = "The overnight batch runs valuation, risk and regulatory extracts. " * 8
    segments = [
        Segment("Slide 1", "Intro", "Short."),
        Segment("Slide 2", "Batch", meaty),
        Segment("Slide 3", "Outro", "Short."),
    ]
    chunks = chunk_segments(segments, target_chars=1100)
    batch = [c for c in chunks if "valuation" in c.text]
    assert len(batch) == 1
    assert batch[0].locator == "Slide 2", "a substantial slide must cite to itself"


# -------------------------------------------------------------- ingestion

def test_ingest_and_retrieve(tmp_path):
    path = _write_sample(tmp_path)
    result = ingest_file(path, DocumentMeta(title="Settlement fails", role="production_support"))
    assert result["chunks"] >= 1

    hits = retrieval.search("why do trades fail to settle")
    assert hits, "expected at least one hit"
    assert any("settle" in hit.text.lower() for hit in hits)


def test_ingest_rejects_duplicate(tmp_path):
    # Dedupe is by content hash, so this body must be unique across the test run.
    from app.ingest.pipeline import DuplicateDocument

    path = _write_sample(
        tmp_path,
        name="dupe.md",
        body="# Novation\n\nNovation replaces a bilateral trade with two trades facing the CCP.",
    )
    ingest_file(path, DocumentMeta(title="Dupe"))
    with pytest.raises(DuplicateDocument):
        ingest_file(path, DocumentMeta(title="Dupe again"))


def test_role_filter_excludes_other_roles(tmp_path):
    path = _write_sample(tmp_path, name="ba-only.md", body="# UAT\n\nUser acceptance testing is run by business users.")
    ingest_file(path, DocumentMeta(title="UAT notes", role="business_analyst"))

    ba_hits = retrieval.search("user acceptance testing", role="business_analyst")
    assert ba_hits
    dev_hits = retrieval.search("user acceptance testing", role="developer")
    assert all(hit.document_title != "UAT notes" for hit in dev_hits)


# ------------------------------------------------------------------- API

def test_status_endpoint(client):
    body = client.get("/api/status").json()
    assert body["embedding_provider"] == "hashing"
    assert body["documents"] >= 1  # the seed pack loaded on startup
    assert "model" in body


def test_seed_pack_indexes_and_is_retrievable(client):
    # Startup only seeds an empty knowledge base, and earlier tests have already
    # written to it, so load explicitly rather than depending on test ordering.
    from app.ingest.seed import load_seed_pack

    load_seed_pack()
    titles = [d["title"] for d in client.get("/api/documents").json()]
    assert any("Trade Lifecycle" in t for t in titles), titles
    assert any("Production Support" in t for t in titles), titles

    hits = retrieval.search("what is the difference between clearing and settlement")
    assert hits
    assert any("clearing" in hit.text.lower() for hit in hits)
    # Locators are what make citations useful - they must survive ingestion.
    assert all(hit.locator for hit in hits)


def test_search_endpoint_returns_citable_hits(client):
    hits = client.get("/api/documents/search/query", params={"q": "what is novation"}).json()
    assert hits
    assert {"document_title", "locator", "text", "score"} <= set(hits[0])


def test_upload_requires_admin_token(client, tmp_path):
    path = _write_sample(tmp_path, name="unauthorised.md")
    with path.open("rb") as handle:
        response = client.post("/api/documents", files={"file": ("unauthorised.md", handle, "text/markdown")})
    assert response.status_code == 401


def test_upload_with_token_indexes(client, tmp_path):
    path = _write_sample(tmp_path, name="authorised.md", body="# Nostro\n\nA nostro is our account with another bank.")
    with path.open("rb") as handle:
        response = client.post(
            "/api/documents",
            files={"file": ("authorised.md", handle, "text/markdown")},
            data={"title": "Nostro note", "role": "general"},
            headers={"X-Admin-Token": "test-token"},
        )
    assert response.status_code == 200, response.text
    document_id = response.json()["document_id"]

    chunks = client.get(f"/api/documents/{document_id}/chunks").json()
    assert chunks["document"]["title"] == "Nostro note"
    assert len(chunks["chunks"]) >= 1

    deleted = client.delete(f"/api/documents/{document_id}", headers={"X-Admin-Token": "test-token"})
    assert deleted.status_code == 200


def test_unsupported_file_type_rejected(client, tmp_path):
    path = tmp_path / "notes.xlsx"
    path.write_bytes(b"not really a spreadsheet")
    with path.open("rb") as handle:
        response = client.post(
            "/api/documents",
            files={"file": ("notes.xlsx", handle, "application/vnd.ms-excel")},
            headers={"X-Admin-Token": "test-token"},
        )
    assert response.status_code == 415


def test_chat_without_api_key_returns_503(client, monkeypatch):
    from app import llm

    monkeypatch.setattr(llm, "is_configured", lambda: False)
    response = client.post("/api/chat/stream", json={"message": "hello"})
    assert response.status_code == 503


def test_split_sections_get_distinguishable_locators():
    # Two citations from one long section must not look like the same source.
    long_body = "Settlement fails accrue CSDR penalties every single day they persist. " * 40
    chunks = chunk_segments([Segment("Stage 7", "Settlement", long_body)], target_chars=500)
    locators = [c.locator for c in chunks]
    assert len(chunks) > 1
    assert len(set(locators)) == len(locators), locators
    assert locators[0] == f"Stage 7 (1/{len(chunks)})"


def test_interview_session_listing_works(client):
    # Exercises the aggregate query (COUNT ... FILTER) without needing an API key.
    response = client.get("/api/interview/sessions")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_replacing_a_document_reindexes_and_cleans_up(client, tmp_path):
    from app.storage import StorageError, get_storage, object_key

    storage = get_storage()
    body = "# Repo\n\nA repo is economically a secured loan against securities."
    path = _write_sample(tmp_path, name="repo.md", body=body)
    headers = {"X-Admin-Token": "test-token"}

    with path.open("rb") as handle:
        first = client.post(
            "/api/documents",
            files={"file": ("repo.md", handle, "text/markdown")},
            data={"title": "Repo v1"},
            headers=headers,
        )
    assert first.status_code == 200, first.text
    old_id = first.json()["document_id"]

    # The original is retained byte-for-byte and downloadable by an admin.
    old_key = object_key(old_id, ".md")
    assert storage.get(old_key) == path.read_bytes()
    download = client.get(f"/api/documents/{old_id}/original", headers=headers)
    assert download.status_code == 200
    assert download.content == path.read_bytes()
    assert client.get(f"/api/documents/{old_id}/original").status_code == 401

    # Same bytes, so it is a duplicate unless replacement is requested.
    with path.open("rb") as handle:
        conflict = client.post(
            "/api/documents",
            files={"file": ("repo.md", handle, "text/markdown")},
            headers=headers,
        )
    assert conflict.status_code == 409

    with path.open("rb") as handle:
        replaced = client.post(
            "/api/documents",
            files={"file": ("repo.md", handle, "text/markdown")},
            data={"title": "Repo v2", "replace_existing": "true"},
            headers=headers,
        )
    assert replaced.status_code == 200, replaced.text
    new_id = replaced.json()["document_id"]
    assert new_id != old_id

    # The superseded original must not be orphaned in the bucket.
    with pytest.raises(StorageError):
        storage.get(old_key)
    assert storage.get(object_key(new_id, ".md")) == path.read_bytes()

    titles = [d["title"] for d in client.get("/api/documents").json()]
    assert "Repo v2" in titles and "Repo v1" not in titles

    # Deleting the document takes its stored original with it.
    assert client.delete(f"/api/documents/{new_id}", headers=headers).status_code == 200
    with pytest.raises(StorageError):
        storage.get(object_key(new_id, ".md"))


def test_older_database_gains_columns_added_after_release():
    """A database created before `object_key` existed must be repaired, not crash.

    CREATE TABLE IF NOT EXISTS silently leaves an existing table alone, so
    without the additive migration every query naming the column fails.
    """
    import sqlite3

    from app.db import Connection, _apply_column_migrations, _existing_columns

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, title TEXT)")
    raw.execute("INSERT INTO documents VALUES ('d1', 'Deck from the old schema')")
    conn = Connection(raw, "sqlite")

    assert "object_key" not in _existing_columns(conn, "documents")
    assert "documents.object_key" in _apply_column_migrations(conn)
    assert "object_key" in _existing_columns(conn, "documents")

    row = conn.execute("SELECT id, title, object_key FROM documents").fetchone()
    assert row["title"] == "Deck from the old schema", "existing rows must survive"
    assert row["object_key"] is None

    assert _apply_column_migrations(conn) == [], "second run must be a no-op"
    raw.close()


def test_sql_placeholders_translate_for_postgres():
    from app.db import Connection

    sqlite_conn = Connection(None, "sqlite")
    pg_conn = Connection(None, "postgres")
    sql = "SELECT * FROM documents WHERE role = ? AND client = ?"
    assert sqlite_conn._sql(sql) == sql
    assert pg_conn._sql(sql) == "SELECT * FROM documents WHERE role = %s AND client = %s"


# ------------------------------------------------------------ LLM provider

def test_gemini_schema_strips_keys_gemini_rejects():
    """Gemini's response_schema is an OpenAPI subset; additionalProperties 400s."""
    from app.llm import _gemini_schema
    from app.prompts import INTERVIEW_FEEDBACK_SCHEMA

    cleaned = _gemini_schema(INTERVIEW_FEEDBACK_SCHEMA)

    def assert_clean(node):
        assert "additionalProperties" not in node
        for child in node.get("properties", {}).values():
            assert_clean(child)
        if "items" in node:
            assert_clean(node["items"])

    assert_clean(cleaned)
    # everything Gemini does support must survive
    assert cleaned["required"] == INTERVIEW_FEEDBACK_SCHEMA["required"]
    assert cleaned["properties"]["verdict"]["enum"] == ["needs_work", "on_track", "strong"]
    assert cleaned["properties"]["gaps"]["items"]["type"] == "string"
    assert "additionalProperties" in INTERVIEW_FEEDBACK_SCHEMA, "must not mutate the original"


def test_gemini_contents_maps_assistant_to_model_role():
    from app.llm import _gemini_contents

    contents = _gemini_contents([
        {"role": "user", "content": "what is novation?"},
        {"role": "assistant", "content": "It replaces a counterparty."},
        {"role": "user", "content": "and netting?"},
    ])
    assert [c.role for c in contents] == ["user", "model", "user"]
    assert contents[1].parts[0].text == "It replaces a counterparty."


def test_provider_selection_and_key_detection(monkeypatch):
    from app import llm
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "llm_provider", "gemini")
    monkeypatch.setattr(settings, "google_api_key", "")
    assert llm.provider() == "gemini"
    assert llm.is_configured() is False
    assert "aistudio.google.com" in llm._missing_key_message()

    monkeypatch.setattr(settings, "google_api_key", "test-key")
    assert llm.is_configured() is True
    assert llm.active_model() == settings.gemini_model

    monkeypatch.setattr(settings, "llm_provider", "anthropic")
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    assert llm.is_configured() is False
    assert "console.anthropic.com" in llm._missing_key_message()
