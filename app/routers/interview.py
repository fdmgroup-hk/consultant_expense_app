"""Mock-interview mode: role-specific questions, scored answers, follow-ups."""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Response

from .. import db, llm
from ..prompts import (
    INTERVIEW_FEEDBACK_SCHEMA,
    INTERVIEW_QUESTION_SCHEMA,
    ROLE_LABELS,
    TROUBLESHOOTING_STEPS,
    build_interview_system,
    format_context,
)
from ..retrieval import search as retrieval
from ..schemas import (
    InterviewAnswerRequest,
    InterviewFeedbackOut,
    InterviewQuestionOut,
    InterviewStartRequest,
    InterviewSummaryOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/interview", tags=["interview"])

#: Whether a question is command-line shaped is decided here, not left to the
#: model - it returned an empty walkthrough for "the first three things you would
#: check on the server", which is exactly the case the feature exists for.
_COMMAND_HINTS = (
    "linux", "unix", "command", "shell", "script", "terminal", "server", "host",
    "log", "logs", "process", "cpu", "memory", "disk", "filesystem", "service",
    "daemon", "systemctl", "journalctl", "grep", "restart", "batch", "job",
    "crontab", "autosys", "control-m", "port", "network",
)


def _is_command_line_question(question: str, topic: str | None) -> bool:
    text = f"{question} {topic or ''}".lower()
    return any(hint in text for hint in _COMMAND_HINTS)


TOPIC_SEEDS = {
    "developer": "developer responsibilities technical stack build deploy trade systems",
    "production_support": "production support incident triage escalation batch monitoring outage",
    "business_analyst": "business analyst requirements stakeholders UAT process trade lifecycle",
    "general": "trade lifecycle order flow technology role responsibilities investment bank",
}


async def _guarded(coro):
    """Await an LLM call, turning provider outages into honest HTTP statuses."""
    try:
        return await coro
    except llm.LLMUnavailable as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


def _require_llm() -> None:
    if not llm.is_configured():
        raise HTTPException(
            status_code=503,
            detail=llm._missing_key_message() + " Then restart the app.",
        )


def _load_session(session_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM interview_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No such interview session.")
    return dict(row)


def _load_turns(session_id: str) -> list[dict[str, Any]]:
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM interview_turns WHERE session_id = ? ORDER BY ordinal",
            (session_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _retrieve(session: dict[str, Any], extra: str = "") -> list:
    """Narrowest useful slice of the knowledge base, widening only if it is empty.

    client + role -> role only -> everything. A consultant who picked HSBC should
    get HSBC material, but an empty question list helps nobody, so each filter is
    dropped in turn rather than returning nothing.
    """
    role = session["role_focus"]
    client = session.get("client_focus") or None
    query = " ".join(
        part for part in [session.get("topic") or "", extra, TOPIC_SEEDS.get(role, "")] if part
    )
    role_filter = role if role != "general" else None

    for attempt in (
        {"role": role_filter, "client": client},
        {"role": role_filter, "client": None},
        {"role": None, "client": None},
    ):
        hits = retrieval.search(query, **attempt)
        if hits:
            return hits
    return []


def _transcript(turns: list[dict[str, Any]]) -> str:
    if not turns:
        return "No questions asked yet - this is the opening question."
    lines = ["INTERVIEW SO FAR"]
    for turn in turns:
        lines.append(f"\nQ{turn['ordinal']}: {turn['question']}")
        if turn.get("answer"):
            lines.append(f"CANDIDATE: {turn['answer']}")
            if turn.get("score") is not None:
                lines.append(f"(you scored that {turn['score']}/10)")
        else:
            lines.append("CANDIDATE: (not answered)")
    return "\n".join(lines)


def _insert_turn(session_id: str, ordinal: int, question: str, kind: str) -> int:
    with db.connection() as conn:
        return conn.insert(
            "INSERT INTO interview_turns (session_id, ordinal, question, question_kind) "
            "VALUES (?, ?, ?, ?)",
            (session_id, ordinal, question, kind),
        )


async def _generate_question(session: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    hits = _retrieve(session, extra=turns[-1]["question"] if turns else "")
    topic_line = (
        f"The consultant asked to focus on: {session['topic']}." if session.get("topic") else
        "No specific topic requested - cover the ground a real interview would."
    )
    asked = "\n".join(f"- {t['question']}" for t in turns) or "(none yet)"

    user_turn = (
        f"{format_context(hits)}\n\n---\n\n{_transcript(turns)}\n\n"
        f"{topic_line}\n\nQuestions already asked (do not repeat or lightly reword these):\n{asked}\n\n"
        "Ask the next question."
    )
    result = await _guarded(llm.structured(
        build_interview_system(session["role_focus"], session["level"], session.get("client_focus")),
        [{"role": "user", "content": user_turn}],
        INTERVIEW_QUESTION_SCHEMA,
    ))
    result["citations"] = [hit.as_citation(i) for i, hit in enumerate(hits, start=1)]
    return result


@router.post("/start", response_model=InterviewQuestionOut)
async def start_interview(request: InterviewStartRequest) -> InterviewQuestionOut:
    _require_llm()
    session_id = str(uuid.uuid4())
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO interview_sessions (id, role_focus, level, topic, client_focus) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, request.role, request.level, request.topic,
             (request.client or "").strip() or None),
        )
    session = _load_session(session_id)

    generated = await _generate_question(session, [])
    turn_id = _insert_turn(session_id, 1, generated["question"], generated.get("kind", "domain"))
    return InterviewQuestionOut(
        session_id=session_id,
        turn_id=turn_id,
        ordinal=1,
        question=generated["question"],
        kind=generated.get("kind", "domain"),
        citations=generated["citations"],
    )


@router.post("/answer", response_model=InterviewFeedbackOut)
async def answer_question(request: InterviewAnswerRequest) -> InterviewFeedbackOut:
    _require_llm()
    session = _load_session(request.session_id)
    turns = _load_turns(request.session_id)
    current = next((t for t in turns if t["id"] == request.turn_id), None)
    if current is None:
        raise HTTPException(status_code=404, detail="No such question in this interview.")
    if current["answer"]:
        raise HTTPException(status_code=409, detail="That question has already been answered.")

    hits = _retrieve(session, extra=f"{current['question']} {request.answer}")
    prior = [t for t in turns if t["ordinal"] < current["ordinal"]]
    command_directive = (
        "THIS IS A COMMAND-LINE QUESTION. command_walkthrough MUST contain 4-8 ordered "
        "steps, each with one runnable command, and minimum_commands MUST contain 5-8 "
        "complete commands. Do not return empty arrays for either."
        if _is_command_line_question(current["question"], session.get("topic"))
        else "THIS IS NOT A COMMAND-LINE QUESTION. Return empty arrays for both "
             "command_walkthrough and minimum_commands."
    )
    user_turn = (
        f"{format_context(hits)}\n\n---\n\n{_transcript(prior)}\n\n"
        f"QUESTION YOU ASKED: {current['question']}\n\n"
        f"CANDIDATE'S ANSWER: {request.answer}\n\n"
        f"{command_directive}\n\n"
        "Assess this answer."
    )
    feedback = await _guarded(llm.structured(
        build_interview_system(session["role_focus"], session["level"], session.get("client_focus")),
        [{"role": "user", "content": user_turn}],
        INTERVIEW_FEEDBACK_SCHEMA,
    ))

    citations = [hit.as_citation(i) for i, hit in enumerate(hits, start=1)]
    stored = dict(feedback)
    stored["citations"] = citations
    with db.connection() as conn:
        conn.execute(
            "UPDATE interview_turns SET answer = ?, score = ?, feedback = ? WHERE id = ?",
            (request.answer, int(feedback["score"]), json.dumps(stored), request.turn_id),
        )

    return InterviewFeedbackOut(
        turn_id=request.turn_id,
        score=int(feedback["score"]),
        verdict=feedback["verdict"],
        strengths=feedback.get("strengths", []),
        must_know=feedback.get("must_know", []),
        good_to_know=feedback.get("good_to_know", []),
        advanced_bonus=feedback.get("advanced_bonus", []),
        process_covered=feedback.get("process_covered", {}) or {},
        command_walkthrough=feedback.get("command_walkthrough", []) or [],
        minimum_commands=feedback.get("minimum_commands", []) or [],
        model_answer=feedback.get("model_answer", ""),
        follow_up_question=feedback.get("follow_up_question", ""),
        citations=citations,
    )


@router.post("/next", response_model=InterviewQuestionOut)
async def next_question(session_id: str, use_follow_up: bool = False) -> InterviewQuestionOut:
    _require_llm()
    session = _load_session(session_id)
    turns = _load_turns(session_id)
    ordinal = len(turns) + 1

    if use_follow_up and turns and turns[-1].get("feedback"):
        stored = json.loads(turns[-1]["feedback"])
        follow_up = (stored.get("follow_up_question") or "").strip()
        if follow_up:
            turn_id = _insert_turn(session_id, ordinal, follow_up, "followup")
            return InterviewQuestionOut(
                session_id=session_id,
                turn_id=turn_id,
                ordinal=ordinal,
                question=follow_up,
                kind="followup",
                citations=stored.get("citations", []),
            )

    generated = await _generate_question(session, turns)
    turn_id = _insert_turn(session_id, ordinal, generated["question"], generated.get("kind", "domain"))
    return InterviewQuestionOut(
        session_id=session_id,
        turn_id=turn_id,
        ordinal=ordinal,
        question=generated["question"],
        kind=generated.get("kind", "domain"),
        citations=generated["citations"],
    )


@router.get("/sessions", response_model=list[dict])
def list_interviews() -> list[dict]:
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT s.id, s.role_focus, s.level, s.topic, s.client_focus, s.status, s.created_at,
                   COUNT(t.id) FILTER (WHERE t.answer IS NOT NULL) AS answered,
                   AVG(t.score) AS average_score
            FROM interview_sessions s
            LEFT JOIN interview_turns t ON t.session_id = s.id
            GROUP BY s.id
            ORDER BY s.created_at DESC
            LIMIT 100
            """
        ).fetchall()
    return [
        {
            **dict(row),
            "role_label": ROLE_LABELS.get(row["role_focus"], row["role_focus"]),
            "average_score": round(float(row["average_score"]), 1) if row["average_score"] else None,
        }
        for row in rows
    ]


@router.get("/sessions/{session_id}", response_model=InterviewSummaryOut)
def interview_summary(session_id: str) -> InterviewSummaryOut:
    session = _load_session(session_id)
    turns = _load_turns(session_id)
    scored = [t["score"] for t in turns if t["score"] is not None]
    return InterviewSummaryOut(
        session_id=session_id,
        role=session["role_focus"],
        level=session["level"],
        topic=session["topic"],
        client=session.get("client_focus"),
        answered=len(scored),
        average_score=round(sum(scored) / len(scored), 1) if scored else None,
        turns=[
            {
                "turn_id": t["id"],
                "ordinal": t["ordinal"],
                "question": t["question"],
                "kind": t["question_kind"],
                "answer": t["answer"],
                "score": t["score"],
                "feedback": json.loads(t["feedback"]) if t["feedback"] else None,
            }
            for t in turns
        ],
    )


def _feedback_markdown(turn: dict[str, Any]) -> list[str]:
    """Render one answered turn's feedback. Sections are omitted when empty so a
    domain question does not carry an empty Commands heading."""
    lines: list[str] = []
    stored = json.loads(turn["feedback"]) if turn.get("feedback") else {}
    if not stored:
        return lines

    verdict = str(stored.get("verdict", "")).replace("_", " ")
    lines.append(f"**Score: {turn['score']}/10 — {verdict}**")
    lines.append("")

    covered = stored.get("process_covered") or {}
    if any(covered.values()):
        hit = [label for key, label in TROUBLESHOOTING_STEPS if covered.get(key)]
        missed = [label for key, label in TROUBLESHOOTING_STEPS if not covered.get(key)]
        lines.append(f"*Troubleshooting order — {len(hit)} of {len(TROUBLESHOOTING_STEPS)}*")
        lines.append(f"- Covered: {', '.join(hit)}")
        if missed:
            lines.append(f"- Missed: {', '.join(missed)}")
        lines.append("")

    for heading, key in (
        ("What worked", "strengths"),
        ("Must know for a junior", "must_know"),
        ("Good to know", "good_to_know"),
        ("Advanced / senior bonus (not expected yet)", "advanced_bonus"),
    ):
        items = stored.get(key) or []
        if items:
            lines.append(f"**{heading}**")
            lines.extend(f"- {item}" for item in items)
            lines.append("")

    walkthrough = stored.get("command_walkthrough") or []
    if walkthrough:
        lines.append("**Commands a strong junior would use**")
        lines.append("")
        for index, step in enumerate(walkthrough, start=1):
            lines.append(f"Step {index} — {step.get('checking', '').strip()}")
            lines.append("")
            lines.append("```bash")
            lines.append(str(step.get("command", "")).strip())
            lines.append("```")
            lines.append("")

    minimum = stored.get("minimum_commands") or []
    if minimum:
        lines.append("**Minimum interview answer — the commands to remember**")
        lines.append("")
        lines.append("```bash")
        lines.extend(str(c).strip() for c in minimum)
        lines.append("```")
        lines.append("")

    if stored.get("model_answer"):
        lines.append("**A strong junior answer**")
        lines.append("")
        lines.append(stored["model_answer"])
        lines.append("")
    return lines


def _session_markdown(session: dict[str, Any], turns: list[dict[str, Any]]) -> str:
    scored = [t["score"] for t in turns if t["score"] is not None]
    average = round(sum(scored) / len(scored), 1) if scored else None
    role = ROLE_LABELS.get(session["role_focus"], session["role_focus"])

    header = [f"# Mock interview — {role}", ""]
    facts = [f"**Level:** {session['level']}"]
    if session.get("client_focus"):
        facts.append(f"**Client:** {session['client_focus']}")
    if session.get("topic"):
        facts.append(f"**Topic:** {session['topic']}")
    facts.append(f"**Answered:** {len(scored)} of {len(turns)}")
    if average is not None:
        facts.append(f"**Average score:** {average}/10")
    if session.get("created_at"):
        facts.append(f"**Date:** {str(session['created_at'])[:16].replace('T', ' ')}")
    header.append("  \n".join(facts))
    header.append("")
    header.append("Scored against junior expectations: senior-level tooling is listed as "
                  "bonus material and does not reduce the score.")
    header.append("")

    body: list[str] = []
    for turn in turns:
        body.append("---")
        body.append("")
        body.append(f"## Question {turn['ordinal']} — {turn.get('question_kind', 'main')}")
        body.append("")
        body.append(turn["question"])
        body.append("")
        if turn.get("answer"):
            body.append("**Your answer**")
            body.append("")
            body.append("> " + turn["answer"].replace("\n", "\n> "))
            body.append("")
            body.extend(_feedback_markdown(turn))
        else:
            body.append("*Not answered.*")
            body.append("")
    return "\n".join(header + body).strip() + "\n"


@router.get("/sessions/{session_id}/export")
def export_interview(session_id: str) -> Response:
    """Download the whole session - questions, answers, scores, commands - as Markdown.

    Markdown rather than PDF so it stays greppable, pastes into Confluence or a
    notes app, and needs no extra dependency in the image.
    """
    session = _load_session(session_id)
    turns = _load_turns(session_id)
    if not turns:
        raise HTTPException(status_code=404, detail="That interview has no questions yet.")

    role = session["role_focus"]
    stamp = str(session.get("created_at") or "")[:10] or "session"
    filename = f"mock-interview-{role}-{stamp}.md"
    return Response(
        content=_session_markdown(session, turns),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/sessions/{session_id}")
def delete_interview(session_id: str) -> dict[str, str]:
    with db.connection() as conn:
        cursor = conn.execute("DELETE FROM interview_sessions WHERE id = ?", (session_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="No such interview session.")
    return {"status": "deleted", "session_id": session_id}
