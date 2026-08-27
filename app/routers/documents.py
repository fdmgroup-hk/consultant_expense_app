"""Knowledge-base endpoints: upload, browse, search, delete, re-index."""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response
from fastapi import UploadFile

from .. import db
from ..ingest.extract import SUPPORTED_EXTENSIONS, UnsupportedFileType
from ..ingest.pipeline import (
    DocumentMeta,
    DuplicateDocument,
    delete_document,
    ingest_file,
    reembed_all,
)
from ..retrieval import search as retrieval
from ..schemas import DocumentOut, IngestResult, SearchHitOut
from ..security import require_admin
from ..storage import StorageError, get_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/documents", tags=["documents"])

MAX_UPLOAD_BYTES = 60 * 1024 * 1024  # 60 MB - comfortably above a slide-heavy deck

_MEDIA_TYPES = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "txt": "text/plain",
}


@router.get("", response_model=list[DocumentOut])
def list_documents(
    role: str | None = Query(default=None),
    client: str | None = Query(default=None),
    department: str | None = Query(default=None),
) -> list[DocumentOut]:
    sql = "SELECT * FROM documents"
    clauses: list[str] = []
    params: list[str] = []
    if role:
        clauses.append("role = ?")
        params.append(role)
    if client:
        clauses.append("client = ?")
        params.append(client)
    if department:
        clauses.append("department = ?")
        params.append(department)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_at DESC, title"

    with db.connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [DocumentOut(**db.row_to_dict(row)) for row in rows]


@router.post("", response_model=IngestResult, dependencies=[Depends(require_admin)])
def upload_document(
    file: UploadFile = File(...),
    title: str = Form(default=""),
    consultant: str = Form(default=""),
    client: str = Form(default=""),
    department: str = Form(default=""),
    role: str = Form(default="general"),
    placement_period: str = Form(default=""),
    tags: str = Form(default=""),
    notes: str = Form(default=""),
    replace_existing: bool = Form(default=False),
) -> IngestResult:
    original_name = Path(file.filename or "upload").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{suffix or original_name}'. "
                   f"Accepted: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    # Buffer to a temp file first. The permanent copy is written by the ingest
    # pipeline through the configured storage backend, which on the hosted
    # deployment is object storage rather than this container's disk.
    with tempfile.TemporaryDirectory(prefix="ce_upload_") as tmpdir:
        staged = Path(tmpdir) / f"upload{suffix}"
        size = 0
        try:
            with staged.open("wb") as target:
                while block := file.file.read(1024 * 1024):
                    size += len(block)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"File is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
                        )
                    target.write(block)

            meta = DocumentMeta(
                title=title.strip() or Path(original_name).stem.replace("_", " ").replace("-", " "),
                consultant=consultant.strip() or None,
                client=client.strip() or None,
                department=department.strip() or None,
                role=role.strip() or "general",
                placement_period=placement_period.strip() or None,
                tags=[t.strip() for t in tags.split(",") if t.strip()],
                notes=notes.strip() or None,
            )
            return IngestResult(**ingest_file(staged, meta, replace_existing=replace_existing))

        except HTTPException:
            raise
        except DuplicateDocument as exc:
            raise HTTPException(
                status_code=409, detail=f"{exc} Tick 'replace existing' to re-index it."
            ) from exc
        except UnsupportedFileType as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except StorageError as exc:
            logger.exception("Storage rejected the upload")
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Ingest failed for %s", original_name)
            raise HTTPException(status_code=500, detail=f"Could not index the file: {exc}") from exc
        finally:
            file.file.close()


@router.delete("/{document_id}", dependencies=[Depends(require_admin)])
def remove_document(document_id: str) -> dict[str, str]:
    if not delete_document(document_id):
        raise HTTPException(status_code=404, detail="No such document.")
    return {"status": "deleted", "document_id": document_id}


@router.get("/{document_id}/chunks")
def document_chunks(document_id: str) -> dict[str, object]:
    with db.connection() as conn:
        document = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if document is None:
            raise HTTPException(status_code=404, detail="No such document.")
        chunks = conn.execute(
            "SELECT id, ordinal, locator, heading, text FROM chunks "
            "WHERE document_id = ? ORDER BY ordinal",
            (document_id,),
        ).fetchall()
    return {"document": db.row_to_dict(document), "chunks": chunks}


@router.get("/{document_id}/original", dependencies=[Depends(require_admin)])
def download_original(document_id: str) -> Response:
    """Fetch the deck as uploaded.

    Admin-gated even though browsing is open: the raw file carries far more than
    the indexed text - embedded images, author metadata, deleted-but-present
    slide content.
    """
    with db.connection() as conn:
        row = conn.execute(
            "SELECT filename, source_type, object_key FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such document.")
    if not row.get("object_key"):
        raise HTTPException(
            status_code=404,
            detail="The original was not retained for this document (STORAGE_BACKEND=none).",
        )

    try:
        payload = get_storage().get(row["object_key"])
    except StorageError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    filename = row.get("filename") or f"{document_id}.{row['source_type']}"
    return Response(
        content=payload,
        media_type=_MEDIA_TYPES.get(row["source_type"], "application/octet-stream"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/search/query", response_model=list[SearchHitOut])
def search_knowledge_base(
    q: str = Query(min_length=1),
    top_k: int = Query(default=10, ge=1, le=50),
    role: str | None = Query(default=None),
    client: str | None = Query(default=None),
    department: str | None = Query(default=None),
) -> list[SearchHitOut]:
    hits = retrieval.search(q, top_k=top_k, role=role, client=client, department=department)
    return [
        SearchHitOut(
            chunk_id=hit.chunk_id,
            document_id=hit.document_id,
            document_title=hit.document_title,
            locator=hit.locator,
            heading=hit.heading,
            text=hit.text,
            client=hit.client,
            department=hit.department,
            role=hit.role,
            consultant=hit.consultant,
            score=hit.score,
            matched_by=hit.matched_by,
        )
        for hit in hits
    ]


@router.post("/reindex", dependencies=[Depends(require_admin)])
def reindex() -> dict[str, object]:
    """Re-embed every chunk with the currently configured embedding model."""
    return reembed_all()
