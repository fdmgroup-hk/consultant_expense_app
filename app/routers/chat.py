"""Grounded chat over the consultant knowledge base, streamed to the browser."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .. import db, llm
from ..prompts import CHAT_SYSTEM, format_context
from ..retrieval import search as retrieval
from ..schemas import ChatMessageOut, ChatRequest, ChatSessionOut

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

HISTORY_TURNS = 12
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # stop nginx buffering the stream
}


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _ensure_session(session_id: str | None, first_message: str, role_focus: str | None) -> str:
    with db.connection() as conn:
        if session_id:
            row = conn.execute(
                "SELECT id FROM chat_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row:
                return session_id
        new_id = session_id or str(uuid.uuid4())
        title = first_message.strip().splitlines()[0][:70] or "New conversation"
        conn.execute(
            "INSERT INTO chat_sessions (id, title, role_focus) VALUES (?, ?, ?)",
            (new_id, title, role_focus),
        )
        return new_id


def _load_history(session_id: str) -> list[dict[str, str]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT role, content FROM chat_messages WHERE session_id = ? "
            "ORDER BY id DESC LIMIT ?",
            (session_id, HISTORY_TURNS),
        ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def _save_message(session_id: str, role: str, content: str, citations: list[dict] | None = None) -> int:
    with db.connection() as conn:
        return conn.insert(
            "INSERT INTO chat_messages (session_id, role, content, citations) VALUES (?, ?, ?, ?)",
            (session_id, role, content, json.dumps(citations or [])),
        )


@router.post("/stream")
async def chat_stream(request: ChatRequest) -> StreamingResponse:
    if not llm.is_configured():
        raise HTTPException(
            status_code=503,
            detail=llm._missing_key_message() + " Then restart the app.",
        )

    session_id = _ensure_session(request.session_id, request.message, request.role)
    history = _load_history(session_id)
    _save_message(session_id, "user", request.message)

    hits = []
    if request.use_knowledge_base:
        # Retrieval reads the last turn too, so "what about the other one?" still
        # pulls sensible excerpts instead of matching on three vague words.
        recent_user = [m["content"] for m in history if m["role"] == "user"][-1:]
        query = " ".join(recent_user + [request.message])
        hits = retrieval.search(
            query, role=request.role, client=request.client,
            department=request.department,
        )

    citations = [hit.as_citation(i) for i, hit in enumerate(hits, start=1)]
    context_block = format_context(hits) if request.use_knowledge_base else (
        "KNOWLEDGE BASE: not used for this question - the consultant asked for general "
        "knowledge only."
    )
    user_turn = f"{context_block}\n\n---\n\nCONSULTANT'S QUESTION: {request.message}"
    messages = history + [{"role": "user", "content": user_turn}]

    async def event_stream() -> AsyncIterator[str]:
        yield _sse({"type": "session", "session_id": session_id})
        if citations:
            yield _sse({"type": "citations", "citations": citations})

        answer_parts: list[str] = []
        try:
            async for event in llm.stream_answer(CHAT_SYSTEM, messages):
                if event["type"] == "text":
                    answer_parts.append(event["text"])
                yield _sse(event)
        except llm.LLMNotConfigured as exc:
            yield _sse({"type": "error", "message": str(exc)})
            return
        except Exception as exc:  # never leave the browser hanging on an open stream
            logger.exception("Chat stream failed")
            yield _sse({"type": "error", "message": f"Unexpected error: {exc}"})
            return

        answer = "".join(answer_parts).strip()
        if answer:
            _save_message(session_id, "assistant", answer, citations)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/sessions", response_model=list[ChatSessionOut])
def list_sessions() -> list[ChatSessionOut]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, title, role_focus, created_at FROM chat_sessions "
            "ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    return [ChatSessionOut(**dict(row)) for row in rows]


@router.get("/sessions/{session_id}", response_model=list[ChatMessageOut])
def session_messages(session_id: str) -> list[ChatMessageOut]:
    with db.connection() as conn:
        exists = conn.execute(
            "SELECT 1 FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="No such conversation.")
        rows = conn.execute(
            "SELECT id, role, content, citations, created_at FROM chat_messages "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
    return [
        ChatMessageOut(
            id=row["id"],
            role=row["role"],
            content=row["content"],
            citations=json.loads(row["citations"] or "[]"),
            created_at=row["created_at"],
        )
        for row in rows
    ]


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str) -> dict[str, str]:
    with db.connection() as conn:
        cursor = conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="No such conversation.")
    return {"status": "deleted", "session_id": session_id}
