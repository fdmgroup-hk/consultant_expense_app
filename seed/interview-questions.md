# Interview Question Bank

Questions that come up repeatedly for technology placements at investment banks, with notes on what the interviewer is actually marking.

## Domain — everyone gets asked these

**Walk me through the trade lifecycle.**
Marked on: whether you can go beyond naming stages. Pick one stage and go deep on what breaks there. Ninety seconds, not five minutes.

**What is the difference between clearing and settlement?**
Clearing manages counterparty risk between execution and settlement — novation to a CCP, margin, netting. Settlement is the actual exchange of cash and securities. A candidate who uses the two words interchangeably has read a glossary and stopped.

**What does T+1 mean, and why did the US move to it?**
Settlement one business day after trade date. The move (May 2024) reduces the window of counterparty and market risk between trade and settlement, and reduces margin held at the CCP. The operational cost is that everything downstream — affirmation, funding, FX for cross-border buyers — has to happen far faster, with much less room to fix a break.

**What is the difference between the front, middle and back office?**
Front office generates revenue: trading, sales, structuring. Middle office manages risk and controls: risk management, product control, trade validation. Back office processes: settlements, confirmations, reconciliation, reporting. Be careful with "back office" as a dismissive term — in interviews, describe it as the control and processing function.

**What is a derivative? Give me an example.**
An instrument whose value derives from an underlying. Futures, options, swaps, forwards. Have one concrete example ready: an interest rate swap where one party pays fixed and receives floating, used to hedge exposure to rate moves.

**What is the difference between a long and a short position?**
Long: you own it and profit if the price rises. Short: you have sold something you do not own, profit if it falls, and you must borrow the security to deliver it — which is why a failed stock loan causes settlement fails.

**What is a settlement fail and what causes one?**
See the trade lifecycle notes. Name at least two causes and say what the consequence is (CSDR penalties in the EU, buy-ins, reputational cost with the client).

**What is reference data and why does it matter?**
The static data describing instruments, counterparties, calendars and settlement instructions. It matters because a trade enriched with wrong reference data will book successfully and fail two days later, far from the point of error.

## Technical — Developer

**Explain the difference between an interface and an abstract class.**
Marked on whether you can say *when you would choose each*, not just list the syntactic differences.

**What is the contract between `equals()` and `hashCode()`?**
If two objects are equal they must have the same hash code. Breaking it means objects vanish inside hash-based collections. Have the failure mode ready, not just the rule.

**Write SQL to find the most recent price for each instrument.**
Window function: `ROW_NUMBER() OVER (PARTITION BY instrument_id ORDER BY price_date DESC)` filtered to `= 1`. A correlated subquery on `MAX(price_date)` also works — mention both and say why the window function is usually cheaper.

**A query that used to run in 2 seconds now takes 4 minutes. How do you investigate?**
Look at the execution plan; check whether an index is being used or a full scan has crept in; check whether data volume grew; check for a function wrapped around an indexed column or an implicit type conversion; check statistics. Say you would compare the plan against a known-good one rather than guessing.

**What is a race condition? Give an example.**
Two threads reading and writing shared state where the result depends on timing. Classic example: check-then-act on a shared counter or cache. Then say how you would fix it — synchronisation, an atomic type, or removing the shared state.

**How would you make a message consumer idempotent?**
Key on a business identifier, record what you have already processed, and make the write an upsert rather than an insert. This is a genuinely common requirement because messaging gives you at-least-once delivery.

**What is the difference between a queue and a topic?**
Queue: one message, one consumer takes it. Topic: every subscriber receives a copy. The follow-up is usually "which would you use for market data, and which for order instructions?" — topic for market data, queue for instructions you must process exactly once.

## Technical — Production Support

**The overnight batch failed at 02:00 and you find out at 07:00. What do you do?**
Assess: which job, what depends on it, is a regulatory or client deadline at risk. Communicate to the business immediately with an ETA. Establish whether it can be restarted from the failure point or needs a full rerun. Escalate early if the deadline is tight. Then RCA.

**How do you find an error in a 2GB log file?**
`grep` with context (`-A`/`-B`), narrow by timestamp, count occurrences to see whether it is a one-off or a flood, then `tail -f` if it is ongoing. Mention Splunk/ELK if the logs are indexed. The interviewer wants to hear a method, not a single command.

**A user says the application is slow. How do you approach it?**
Establish scope (one user or all), confirm against monitoring rather than taking "slow" at face value, check CPU/memory/disk/network on the host, check the database for blocking or long-running queries, check whether an upstream dependency is degraded, check what changed. Say what you would rule out first and why.

**What is the difference between an incident and a problem?**
Incident: restore service. Problem: eliminate the cause. They run in parallel and are tracked separately, deliberately.

**When would you restart a production process without knowing the cause?**
When service restoration is time-critical and you have exhausted quick diagnosis — but capture the evidence first (thread dump, logs, state) so the RCA is still possible. Say that explicitly.

**Would you ever run an UPDATE directly on a production database?**
Not without an approved change record and, in most banks, not personally at all — segregation of duties. This question is a control test. Answering "yes, if it's a quick fix" is a fail.

## Technical — Business Analyst

**What makes a good requirement?**
Clear, testable, states the problem not the solution, has a stated business benefit, and covers the failure paths. Give a bad example and improve it live — that is far more convincing than a definition.

**How do you handle a stakeholder who keeps changing their mind?**
Get decisions documented and signed off; use change control with an impact assessment; find out whether the churn is really an unresolved disagreement between two stakeholders, and if so get the accountable person to decide.

**Walk me through how you would gather requirements for a new settlement report.**
Who uses it and for what decision; what they do today and what is wrong with it; what fields, what grain, what frequency, what cut-off; what the data source is and whether it is trusted; what happens when data is missing; how it will be tested and signed off.

**What is the difference between SIT and UAT?**
SIT proves the systems integrate correctly and is run by technology. UAT proves the solution meets the business need and is run by business users. Different question, different people, different exit criteria.

**How would you prioritise a backlog with more requests than capacity?**
MoSCoW or value-versus-effort, but the real answer is that you make the trade-off visible to the accountable stakeholder and let them choose, rather than deciding quietly yourself.

## Competency — all roles

These use the STAR structure: **S**ituation, **T**ask, **A**ction, **R**esult. Keep Situation and Task short; most of your airtime should be Action, and always finish with a Result — ideally quantified.

- Tell me about a time you worked in a team where something went wrong.
- Tell me about a time you had to learn something complicated quickly.
- Describe a time you disagreed with someone more senior.
- Tell me about a mistake you made.
- Give me an example of when you had to explain something technical to a non-technical audience.
- Tell me about a time you had competing deadlines.
- Why banking? Why technology? Why this role?

**On "tell me about a mistake":** interviewers distrust candidates who claim not to have made one, and distrust the humblebrag ("I work too hard"). Pick something genuine, small enough to be safe and real enough to be credible, own it without excessive apology, and spend most of the answer on what you changed as a result.

**On "why banking":** connect it to something specific. "I like that the systems have a real deadline attached — if the settlement extract does not go out, someone's trade fails" beats "I am passionate about financial markets."

## Questions to ask them

Asking nothing signals no interest. Have three ready, and make at least one specific to what they told you during the interview.

- Where does this team sit in the trade lifecycle, and who are your main users?
- What does a normal day look like versus a bad day?
- What would you want someone in this role to have achieved after six months?
- What is the biggest change coming for this team in the next year?
- How is the team structured between run-the-bank and change-the-bank work?
