"""Copy a local SQLite knowledge base into Postgres (and originals into S3).

Run this once when moving from local development to the hosted deployment, so
the decks already indexed on your machine carry over instead of being re-uploaded.

    # dry run first - shows what would move, changes nothing
    python -m scripts.migrate_to_postgres --dry-run

    # then for real
    set DATABASE_URL=postgresql://...        (Windows: set, macOS/Linux: export)
    python -m scripts.migrate_to_postgres --with-originals

Safe to re-run: documents already present in the target (matched on their
content hash) are skipped rather than duplicated.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.storage import get_storage, object_key  # noqa: E402

TABLES_HISTORY = ("chat_sessions", "chat_messages", "interview_sessions", "interview_turns")


def open_sqlite(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise SystemExit(f"No SQLite database at {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_documents(src: sqlite3.Connection, *, dry_run: bool, with_originals: bool) -> tuple[int, int]:
    settings = get_settings()
    documents = src.execute("SELECT * FROM documents ORDER BY created_at").fetchall()
    moved = skipped = 0

    for doc in documents:
        doc = dict(doc)
        with db.connection() as dest:
            exists = dest.execute(
                "SELECT id FROM documents WHERE sha256 = ?", (doc["sha256"],)
            ).fetchone()
        if exists:
            print(f"  skip     {doc['title'][:52]:<52} (already there)")
            skipped += 1
            continue

        chunks = src.execute(
            "SELECT * FROM chunks WHERE document_id = ? ORDER BY ordinal", (doc["id"],)
        ).fetchall()
        embeddings = {
            row["chunk_id"]: row
            for row in src.execute(
                "SELECT e.* FROM embeddings e JOIN chunks c ON c.id = e.chunk_id "
                "WHERE c.document_id = ?",
                (doc["id"],),
            ).fetchall()
        }

        if dry_run:
            print(f"  would move {doc['title'][:50]:<50} ({len(chunks)} chunks)")
            moved += 1
            continue

        # Push the original up first; a failure here should abort before the row
        # is written, not leave a document pointing at a file that is not there.
        stored_key = doc.get("object_key")
        if with_originals:
            suffix = f".{doc['source_type']}"
            local = None
            if stored_key:
                candidate = settings.upload_dir / stored_key
                local = candidate if candidate.is_file() else None
            if local is None:  # an older local database kept originals flat
                candidates = list(settings.upload_dir.glob(f"{doc['id']}.*"))
                local = candidates[0] if candidates else None
            stored_key = (
                get_storage().put(object_key(doc["id"], suffix), local)
                if local and local.is_file() else None
            )

        with db.connection() as dest:
            dest.execute(
                """
                INSERT INTO documents
                    (id, title, filename, source_type, consultant, client, role,
                     placement_period, tags, notes, sha256, object_key, n_chunks, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc["id"], doc["title"], doc["filename"], doc["source_type"],
                    doc["consultant"], doc["client"], doc["role"], doc["placement_period"],
                    doc["tags"] or "[]", doc["notes"], doc["sha256"], stored_key,
                    doc["n_chunks"], doc["status"],
                ),
            )

            for chunk in chunks:
                new_id = dest.insert(
                    "INSERT INTO chunks (document_id, ordinal, locator, heading, text) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (doc["id"], chunk["ordinal"], chunk["locator"], chunk["heading"], chunk["text"]),
                )
                emb = embeddings.get(chunk["id"])
                if emb is not None:
                    dest.execute(
                        "INSERT INTO embeddings (chunk_id, provider, model, dim, vector) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (new_id, emb["provider"], emb["model"], emb["dim"], bytes(emb["vector"])),
                    )
            db.bump_kb_version(dest)

        print(f"  moved    {doc['title'][:52]:<52} ({len(chunks)} chunks)")
        moved += 1

    return moved, skipped


def migrate_history(src: sqlite3.Connection, dry_run: bool) -> int:
    """Chat and interview history. Ids are regenerated, so links are rebuilt."""
    total = 0
    sessions = src.execute("SELECT * FROM chat_sessions").fetchall()
    interviews = src.execute("SELECT * FROM interview_sessions").fetchall()
    if dry_run:
        print(f"  would move {len(sessions)} conversations and {len(interviews)} interviews")
        return len(sessions) + len(interviews)

    with db.connection() as dest:
        for row in sessions:
            dest.execute(
                "INSERT INTO chat_sessions (id, title, role_focus) VALUES (?, ?, ?) "
                "ON CONFLICT (id) DO NOTHING",
                (row["id"], row["title"], row["role_focus"]),
            )
            for msg in src.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id", (row["id"],)
            ):
                dest.execute(
                    "INSERT INTO chat_messages (session_id, role, content, citations) "
                    "VALUES (?, ?, ?, ?)",
                    (row["id"], msg["role"], msg["content"], msg["citations"]),
                )
            total += 1

        for row in interviews:
            dest.execute(
                "INSERT INTO interview_sessions (id, role_focus, level, topic, status) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
                (row["id"], row["role_focus"], row["level"], row["topic"], row["status"]),
            )
            for turn in src.execute(
                "SELECT * FROM interview_turns WHERE session_id = ? ORDER BY ordinal", (row["id"],)
            ):
                dest.execute(
                    "INSERT INTO interview_turns "
                    "(session_id, ordinal, question, question_kind, answer, score, feedback) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (row["id"], turn["ordinal"], turn["question"], turn["question_kind"],
                     turn["answer"], turn["score"], turn["feedback"]),
                )
            total += 1
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description="Move a local SQLite knowledge base into Postgres.")
    parser.add_argument("--sqlite", type=Path, default=None, help="Source .db (default: data/consultant_experience.db)")
    parser.add_argument("--with-originals", action="store_true", help="Also upload the original files to object storage.")
    parser.add_argument("--with-history", action="store_true", help="Also copy chat and interview history.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would move; change nothing.")
    args = parser.parse_args()

    settings = get_settings()
    if not db.is_postgres():
        raise SystemExit(
            "DATABASE_URL is not set to a postgresql:// URL, so there is nothing to migrate into.\n"
            "Set it to your Supabase connection string and run this again."
        )

    source = args.sqlite or settings.db_path
    print(f"Source: {source}")
    print(f"Target: Postgres ({settings.database_url.split('@')[-1]})")
    print(f"Originals: {get_storage().describe() if args.with_originals else 'not copied'}\n")

    src = open_sqlite(source)
    if not args.dry_run:
        db.init_db()

    try:
        moved, skipped = migrate_documents(
            src, dry_run=args.dry_run, with_originals=args.with_originals
        )
        history = migrate_history(src, args.dry_run) if args.with_history else 0
    finally:
        src.close()
        db.close_pool()  # otherwise psycopg complains about live pool threads at exit

    verb = "Would move" if args.dry_run else "Moved"
    print(f"\n{verb} {moved} document(s), skipped {skipped} already present.")
    if args.with_history:
        print(f"{verb} {history} session(s) of history.")
    if args.dry_run:
        print("\nDry run - nothing was written. Re-run without --dry-run to apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
