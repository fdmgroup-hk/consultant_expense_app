# Production Support Placement — What the Job Actually Is

Also called Application Support, Application Production Services, or Run-the-Bank. You keep systems that people depend on running, and when they stop, you are the one who works out why.

## The shape of the day

Trading systems have a rhythm, and support work is organised around it.

- **Start of day (SOD).** Before the market opens: check the overnight batch finished, positions loaded, static data refreshed, feeds connected, FIX sessions logged on. Anything broken must be fixed *before* the desk starts trading, not after.
- **Intraday.** Monitor, respond to user queries, triage alerts, work on tickets and small fixes between incidents.
- **End of day (EOD).** Market close, trade capture cut-off, feeds to downstream systems, the start of the overnight batch.
- **Overnight batch.** Valuation, risk, P&L, reconciliation, regulatory extracts. A job failing at 02:00 becomes a problem at 07:00 if nobody is on it.
- **Handover.** Follow-the-sun teams pass open issues between Hong Kong, London and New York. A clear handover note is a real skill.

## Incident management — the vocabulary interviewers expect

- **Severity / priority.** Sev 1 is typically "trading is stopped or a regulatory deadline is at risk"; Sev 3 is "one user is inconvenienced". You should be able to justify a severity, not just quote a definition.
- **Incident vs problem.** An **incident** is the outage — restore service. A **problem** is the underlying cause — stop it recurring. They are handled by different processes, deliberately.
- **MTTR** — mean time to restore. Support teams are measured on it.
- **Escalation.** Technical escalation (get a developer or a DBA), and management escalation (the business needs to know). Knowing *when* to escalate is more important than knowing how.
- **RCA / post-incident review.** A written root-cause analysis with a timeline, the cause, the fix, and preventative actions.
- **Workaround vs fix.** Restoring service with a workaround and raising a problem record for the real fix is usually the correct call during market hours.
- **Change-related incidents.** The first question after any incident is "what changed?" — a very high proportion of outages follow a change.

## The critical judgement call

> **Restore service first. Diagnose second — but preserve the evidence.**

If a process is stuck and the desk cannot trade, you restart it. Before you do, take the thread dump, copy the logs, capture the state. Restarting without evidence means you will be back tomorrow with no idea why.

Being able to articulate that trade-off is one of the strongest things you can say in a production-support interview.

## Technical skills you will use daily

### Linux

You will live on the command line. Be genuinely comfortable with:

```bash
grep -i "error" app.log | tail -50        # find recent errors
grep -c "ORDER_REJECTED" app.log          # count occurrences
tail -f app.log                           # watch a log live
find /apps/logs -name "*.log" -mtime -1   # files changed in the last day
ps -ef | grep java                        # is the process running?
top / htop                                # CPU and memory pressure
df -h                                     # disk full is a classic cause
netstat -an | grep 9443                   # is the port listening?
free -m                                   # memory
awk '{print $3}' file | sort | uniq -c    # quick frequency count
```

Also: `sed` for quick edits, `journalctl`/`systemctl` on newer boxes, `crontab -l`, and enough `vi` to read a config file without breaking it.

### SQL

Support SQL is investigative, not analytical:

- Find a trade by its id and read its full state and audit history.
- Compare counts between two systems to locate a gap.
- Find records stuck in a pending status for longer than expected.
- Check whether a static-data record exists and when it was last updated.

Be careful, and say so in the interview: **read-only unless you have an approved change**. Running an `UPDATE` on a production database to "just fix one row" without a change record is a serious control breach, and interviewers ask about this deliberately.

### Job scheduling and batch

Autosys, Control-M or Airflow. Understand: dependencies between jobs, what a "job on hold" means, how to restart a failed job from the correct point (restarting from the beginning may double-count), and the difference between a job that failed and a job that never started because its predecessor did not finish.

### Monitoring and logs

Splunk or ELK queries, Grafana dashboards, alert thresholds. Know the difference between an alert that means "act now" and one that is noise — alert fatigue is a real operational risk, and suggesting a threshold tune is a mature answer.

## Scenario questions — how to answer them

Interviewers give you a scenario and watch how you think. The structure that works:

1. **Assess impact.** Who is affected, is trading stopped, is a regulatory deadline at risk?
2. **Communicate.** Tell the users and open the incident record. Do this early, not after you have fixed it.
3. **Gather evidence.** Logs, monitoring, recent changes, whether it affects one user or everyone.
4. **Form and test a hypothesis.** Narrow it down deliberately rather than restarting things at random.
5. **Restore.** Workaround if that is faster; escalate if you have hit the limit of your access or knowledge.
6. **Follow up.** RCA, problem record, preventative action.

### Worked example

> "Traders report that the blotter is not updating. What do you do?"

"First, how wide is it — one trader, one desk, or everyone? That tells me whether I am looking at a client-side problem or a server-side one. I would confirm the market is open and other systems are fine, then check whether the application is up, whether it is consuming from its message queue, and whether the queue depth is growing — a growing queue with a live consumer usually means the consumer is stuck or slow rather than dead. In parallel I would check what changed overnight and whether the upstream feed is still publishing.

While diagnosing, I would tell the desk what I know and give them an update time, because a trader with no information will escalate. If the desk cannot trade, I would raise it as a high-severity incident straight away rather than waiting until I understood the cause.

If I found the consumer thread hung, I would take a thread dump first so we can find the root cause afterwards, then restart the process to restore service, confirm with the desk that the blotter is moving again, and raise a problem record to get the hang fixed properly."

That answer demonstrates impact assessment, communication, structured diagnosis, evidence preservation and follow-up — which is exactly the checklist the interviewer is marking against.

## Competency questions specific to this role

- Tell me about a time you had to work under pressure with incomplete information.
- Tell me about a time you had to explain something technical to a non-technical person.
- How do you prioritise when three things break at once?
- Tell me about a mistake you made and what you did about it.

For the last one: pick something real, take responsibility, and focus on what you changed afterwards. Interviewers distrust candidates who cannot name a mistake.

## Questions worth asking your interviewer

- What does the alerting look like — how much is automated versus manual checks?
- How does the team split run-the-bank work from project work?
- What is the on-call arrangement?
- What is the most common recurring incident, and is there a problem record open on it?
