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
    is_coding_topic,
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

#: Markdown assembly for coding exercises, kept as names so the question builder
#: below reads as prose rather than as escape sequences.
_BLANK = "\n\n"


def _fence(body: str) -> str:
    return "```\n" + body + "\n```"


def _is_command_line_question(question: str, topic: str | None) -> bool:
    # A coding session is never a command-line session. Without this guard a Java
    # exercise mentioning a "process" or a "log file" collects a Linux walkthrough
    # it has no use for - _COMMAND_HINTS matches on those words alone.
    if is_coding_topic(topic):
        return False
    text = f"{question} {topic or ''}".lower()
    return any(hint in text for hint in _COMMAND_HINTS)


#: Score bands, copied from INTERVIEW_FEEDBACK_SCHEMA. The model is told these and
#: still contradicts them - a live run scored an O(n^2) answer 5 and labelled it
#: "strong" - so the label is derived here instead of trusted.
def _verdict_for(score: int) -> str:
    if score <= 5:
        return "needs_work"
    return "on_track" if score <= 7 else "strong"


def _split_code_answer(feedback: dict[str, Any]) -> None:
    """Move a fenced solution out of model_answer when the model put it there.

    model_answer and model_solution both ask for "the good answer", so on a coding
    question the model fills one or the other. Rather than lose the reference
    solution, take whichever arrived.
    """
    if (feedback.get("model_solution") or "").strip():
        return
    answer = (feedback.get("model_answer") or "").strip()
    if "```" in answer:
        feedback["model_solution"] = answer
        feedback["model_answer"] = ""


def _coding_shape(ordinal: int) -> str:
    """Concept, exercise, concept, exercise...

    Fixed here rather than left to the model, for the same reason the command-line
    trigger is: asked to "focus on Java" it will drift to whatever the retrieved
    job spec mentions and never set an exercise at all.
    """
    return "exercise" if ordinal % 2 == 0 else "concept"


def _format_exercise(result: dict[str, Any]) -> str:
    """Fold the structured exercise fields into the question text.

    Signature, examples and target come back as separate fields so the schema can
    force them to exist, but only the question string is stored, reloaded and
    exported - so they are folded into it as Markdown rather than becoming three
    more columns on interview_turns.
    """
    parts = [result["question"].strip()]
    signature = (result.get("starter_signature") or "").strip()
    examples = (result.get("examples") or "").strip()
    target = (result.get("complexity_target") or "").strip()
    if signature:
        parts.append("**Signature**" + _BLANK + _fence(signature))
    if examples:
        parts.append("**Examples**" + _BLANK + _fence(examples))
    if target:
        parts.append(f"**Target** {target}")
    return _BLANK.join(parts)


TOPIC_SEEDS = {
    "developer": "developer responsibilities technical stack build deploy trade systems",
    "production_support": "production support incident triage escalation batch monitoring outage",
    "business_analyst": "business analyst requirements stakeholders UAT process trade lifecycle",
    "business_analyst_tech": "business analyst SQL data mapping interface API defect testing requirements",
    "business_analyst_non_tech": "business analyst requirements workshops process mapping stakeholders UAT operating model",
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
    # Coding sessions are deliberately ungrounded. The excerpts are handover decks
    # and job specs; retrieving them for a Java session is what produced a JDBC
    # resource-handling question anchored to a Societe Generale role description,
    # and citing them under a LeetCode exercise would be noise.
    if is_coding_topic(session.get("topic")):
        return []

    role = session["role_focus"]
    client = session.get("client_focus") or None
    department = session.get("department_focus") or None
    query = " ".join(
        part for part in [session.get("topic") or "", extra, TOPIC_SEEDS.get(role, "")] if part
    )
    role_filter = role if role != "general" else None

    for attempt in (
        {"role": role_filter, "client": client, "department": department},
        {"role": role_filter, "client": client, "department": None},
        {"role": role_filter, "client": None, "department": None},
        {"role": None, "client": None, "department": None},
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


_CONCEPT_DIRECTIVE = (
    "ASK A CONCEPT QUESTION this turn. One language or platform question, answered "
    "in words, no code to write. Leave starter_signature, examples and "
    "complexity_target as empty strings. Set kind to 'technical'."
)

_EXERCISE_DIRECTIVE = (
    "ASK A CODING EXERCISE this turn. Set kind to 'coding'. The question field holds "
    "the problem statement only - one short paragraph, generic, no bank or client "
    "framing. starter_signature MUST hold the one-line signature to implement, "
    "examples MUST hold one or two 'Input: ... -> Output: ...' lines with literal "
    "values, and complexity_target MUST hold the target, e.g. 'O(n) time, O(n) space'. "
    "None of those three may be empty."
)


async def _generate_question(session: dict[str, Any], turns: list[dict[str, Any]]) -> dict[str, Any]:
    hits = _retrieve(session, extra=turns[-1]["question"] if turns else "")
    topic = session.get("topic")
    coding = is_coding_topic(topic)
    topic_line = (
        f"The consultant asked to focus on: {topic}." if topic else
        "No specific topic requested - cover the ground a real interview would."
    )
    asked = "\n".join(f"- {t['question']}" for t in turns) or "(none yet)"

    if coding:
        shape = _coding_shape(len(turns) + 1)
        context = (
            "NO KNOWLEDGE BASE FOR THIS QUESTION - a coding topic was requested, so "
            "the question is a standard one asked from general knowledge. Do not "
            "mention handover decks or missing material."
        )
        directive = _EXERCISE_DIRECTIVE if shape == "exercise" else _CONCEPT_DIRECTIVE
    else:
        context = format_context(hits)
        directive = (
            "This is not a coding session. Leave starter_signature, examples and "
            "complexity_target as empty strings."
        )

    user_turn = (
        f"{context}\n\n---\n\n{_transcript(turns)}\n\n"
        f"{topic_line}\n\nQuestions already asked (do not repeat or lightly reword these):\n{asked}\n\n"
        f"{directive}\n\n"
        "Ask the next question."
    )
    result = await _guarded(llm.structured(
        build_interview_system(
            session["role_focus"], session["level"], session.get("client_focus"), topic,
            session.get("department_focus"),
        ),
        [{"role": "user", "content": user_turn}],
        INTERVIEW_QUESTION_SCHEMA,
    ))
    if coding and _coding_shape(len(turns) + 1) == "exercise":
        result["question"] = _format_exercise(result)
        result["kind"] = "coding"
    result["citations"] = [hit.as_citation(i) for i, hit in enumerate(hits, start=1)]
    return result


@router.post("/start", response_model=InterviewQuestionOut)
async def start_interview(request: InterviewStartRequest) -> InterviewQuestionOut:
    _require_llm()
    session_id = str(uuid.uuid4())
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO interview_sessions "
            "(id, role_focus, level, topic, client_focus, department_focus) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (session_id, request.role, request.level, request.topic,
             (request.client or "").strip() or None,
             (request.department or "").strip() or None),
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
    # Whether code was asked for is a fact about the stored turn, not a judgement
    # call - the same reason the command-line trigger moved into the router.
    code_directive = (
        "THE CANDIDATE WAS ASKED TO WRITE CODE. Review it as a coding screen does: "
        "code_correctness MUST say whether it actually returns the right answer and "
        "name the input that breaks it if not, complexity_verdict MUST compare their "
        "complexity with the target, edge_cases_missed MUST list only cases they "
        "genuinely missed, and model_solution MUST contain a complete runnable "
        "solution in a fenced code block. The reference code goes in model_solution "
        "and NOWHERE ELSE - model_answer holds at most two sentences naming the "
        "approach, with no code block in it. Correctness outranks style: code that "
        "does not work cannot score above 4. Leave process_covered all false."
        if current.get("question_kind") == "coding"
        else "THIS IS NOT A CODING EXERCISE. Return empty strings for "
             "code_correctness, complexity_verdict and model_solution, and an empty "
             "array for edge_cases_missed."
    )
    context = (
        "NO KNOWLEDGE BASE FOR THIS QUESTION - a coding topic was requested. Assess "
        "from general knowledge and do not mention handover decks."
        if is_coding_topic(session.get("topic")) else format_context(hits)
    )
    user_turn = (
        f"{context}\n\n---\n\n{_transcript(prior)}\n\n"
        f"QUESTION YOU ASKED: {current['question']}\n\n"
        f"CANDIDATE'S ANSWER: {request.answer}\n\n"
        f"{command_directive}\n\n{code_directive}\n\n"
        "Assess this answer."
    )
    feedback = await _guarded(llm.structured(
        build_interview_system(
            session["role_focus"], session["level"], session.get("client_focus"),
            session.get("topic"), session.get("department_focus"),
        ),
        [{"role": "user", "content": user_turn}],
        INTERVIEW_FEEDBACK_SCHEMA,
    ))

    feedback["verdict"] = _verdict_for(int(feedback["score"]))
    if current.get("question_kind") == "coding":
        _split_code_answer(feedback)

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
        code_correctness=feedback.get("code_correctness", "") or "",
        complexity_verdict=feedback.get("complexity_verdict", "") or "",
        edge_cases_missed=feedback.get("edge_cases_missed", []) or [],
        model_solution=feedback.get("model_solution", "") or "",
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
        department=session.get("department_focus"),
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

    for heading, key in (
        ("Does it work?", "code_correctness"),
        ("Complexity", "complexity_verdict"),
    ):
        value = (stored.get(key) or "").strip()
        if value:
            lines.append(f"**{heading}** {value}")
            lines.append("")

    edge_cases = stored.get("edge_cases_missed") or []
    if edge_cases:
        lines.append("**Edge cases missed**")
        lines.extend(f"- {case}" for case in edge_cases)
        lines.append("")

    if stored.get("model_solution"):
        lines.append("**Reference solution**")
        lines.append("")
        lines.append(stored["model_solution"])
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
    if session.get("department_focus"):
        facts.append(f"**Department:** {session['department_focus']}")
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
