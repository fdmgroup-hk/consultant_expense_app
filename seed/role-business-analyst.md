# Business Analyst Placement — What the Job Actually Is

The BA sits between people who know what the business needs and people who build it. On a bank technology team that means translating in both directions, and being the person who notices the question nobody asked.

## What you will be doing

- Sitting with traders, operations staff or risk analysts to understand a process, then writing it down accurately enough that a developer can build from it.
- Writing user stories and acceptance criteria, and defending them in refinement sessions.
- Producing process maps of how something works today ("as-is") and how it should work ("to-be").
- Analysing data to size a problem: how many trades actually hit this edge case, and does it matter?
- Coordinating UAT: writing test cases, walking business users through them, triaging what they raise.
- Chasing decisions. A surprising amount of the job is noticing that two stakeholders disagree and getting them in a room.

## Requirements — the part people do badly

A requirement says **what** the business needs and **why**. It does not say how to build it — that is the technical team's job, and specifying the solution in the requirement removes their ability to find a better one.

**Weak:** "Add a dropdown to the trade screen with the settlement currency."

**Strong:** "Operations currently cannot see the settlement currency at the point of booking, so around 30 trades a month are booked with the wrong SSI and have to be amended after the fact. Users need the settlement currency visible and confirmable before a trade is submitted."

The second version states the problem, quantifies it, and leaves the design open.

### User stories and acceptance criteria

The familiar format:

> As a *[role]*, I want *[capability]*, so that *[benefit]*.

The "so that" is the part that gets dropped and is the part that matters — it is what lets someone challenge whether the story is worth building.

Acceptance criteria are testable statements. Given/When/Then is the common structure:

> **Given** a trade in a currency the counterparty has no SSI for,
> **When** the user submits the trade,
> **Then** the trade is rejected with a message naming the missing SSI, and the trade is not written to the booking system.

Good acceptance criteria cover the unhappy paths, not just the happy one. "What happens if it fails?" is the question that separates a competent BA from a note-taker.

### INVEST

A well-formed story is Independent, Negotiable, Valuable, Estimable, Small, Testable. Worth knowing by name — it comes up.

### MoSCoW

Must have, Should have, Could have, Won't have (this time). A prioritisation framework interviewers like because it forces the "won't" conversation.

## Elicitation — getting the requirement out of someone's head

Techniques you should be able to name and, better, give an example of using:

- **Interviews** — one to one, open questions first, then specifics.
- **Workshops** — good for getting conflicting stakeholders to reconcile in one session rather than over six weeks of email.
- **Observation / job shadowing** — sit with an operations analyst for a morning. You will see the workaround they never thought to mention, which is usually the real requirement.
- **Document analysis** — existing procedures, old specs, the spreadsheet someone maintains by hand.
- **Data analysis** — query the system to find out how often the edge case actually happens.
- **Prototyping** — a wireframe gets a reaction that a paragraph never will.

The single most useful habit: **ask "why" until you reach the actual problem**. A stakeholder who asks for a new report often needs an answer to one question, not a report.

## Documents and artefacts

- **BRD (Business Requirements Document)** — the business problem and needs.
- **FSD (Functional Specification)** — what the system will do about it.
- **Process maps / BPMN** — swimlane diagrams showing who does what and where the handoffs are. Handoffs are where processes break.
- **Data mapping** — source field to target field, with the transformation rule. Tedious, and the thing that most often goes wrong in a migration.
- **RAID log** — Risks, Assumptions, Issues, Dependencies.
- **RACI** — who is Responsible, Accountable, Consulted, Informed. Useful when nobody will make a decision.
- **Traceability matrix** — every requirement maps to a test case. Auditors ask for this.

## Stakeholder management

You will have stakeholders who disagree, and part of the job is surfacing that rather than papering over it. Interviewers ask about it because it is the hardest part of the role.

Be ready for:

- **A stakeholder who will not engage.** Escalate through the project manager, but first try meeting them on their terms — sit on the desk, use their vocabulary, bring something concrete rather than a blank page.
- **Two stakeholders who want incompatible things.** Get the decision made by the person accountable, document it, and record the trade-off in the RAID log.
- **Scope creep.** Every change goes through change control with an impact assessment. Saying "yes, and here is what it costs" works better than saying no.

## Testing and UAT

- **SIT (System Integration Testing)** — do the systems talk to each other correctly? Usually run by the technology team.
- **UAT (User Acceptance Testing)** — does this actually solve the business problem? Run by business users, coordinated by the BA.
- **Regression testing** — did we break something that used to work?
- **Defect triage** — is it a defect (it does not meet the agreed spec) or a change request (the spec was wrong)? That distinction decides who pays for it, so it gets argued about.

Writing a good test case is a BA skill: preconditions, steps, expected result, and test data that actually exercises the rule.

## Technical skills expected

You are not expected to code, but you are expected to be self-sufficient with data.

- **SQL.** Genuinely required. Joins, aggregation, filtering, and enough confidence to answer your own question rather than raising a ticket for it.
- **Excel.** Pivot tables, `VLOOKUP`/`XLOOKUP`, `SUMIFS`, and enough discipline not to build a business-critical process on a spreadsheet.
- **Reading a message.** Being able to look at a FIX message, an XML payload or a JSON API response and identify the field under discussion.
- **JIRA and Confluence.** Universal in banks.
- **Understanding of the trade lifecycle.** This is what makes you useful rather than generic. A BA who knows what settlement is writes better requirements than one who does not.

## Interview questions to prepare

- How do you gather requirements from a stakeholder who is too busy to meet you?
- What makes a requirement good? Give me an example of a bad one you have seen.
- Tell me about a time two stakeholders disagreed. What did you do?
- How do you prioritise a backlog?
- Walk me through how you would approach UAT for a new settlement report.
- What is the difference between a business requirement and a functional requirement?
- How would you document the difference between an as-is and a to-be process?

## What separates a strong candidate

Curiosity that shows up as specific questions. In the interview itself, ask about the actual process — "who currently does that step manually?", "what happens when it fails?", "how do you know when it has gone wrong?" A BA candidate who interrogates the interviewer's example is demonstrating the job rather than describing it.
