# Developer Placement — What the Job Actually Is

## What you will be doing

You join an existing team maintaining and extending systems that are already in production and already have users who care. Very little of the work is greenfield. A realistic first three months looks like: getting your environment working, picking up small defects, reading a lot of unfamiliar code, and shipping your first change behind someone else's review.

Typical work items:

- Add a field to a message and carry it through three services and a database.
- Fix a bug where a trade with a particular product type is enriched incorrectly.
- Write a batch job that produces a daily extract for a downstream team.
- Add validation so a bad input is rejected at the boundary rather than three systems later.
- Improve a query that has started timing out as data volumes grew.
- Support a release: run the deployment, watch the logs, be available if it goes wrong.

## The stack you are likely to meet

- **Languages.** Java is the most common on the sell side, then Python, C# on some desks, and increasingly TypeScript for front ends. SQL is not optional in any of them.
- **Databases.** Oracle and SQL Server are everywhere in banks; PostgreSQL is growing. Expect large tables, overnight batches, and queries that were written in 2011.
- **Messaging.** Kafka, IBM MQ, Solace, TIBCO EMS. Understanding the difference between a queue (one consumer takes the message) and a topic (every subscriber gets it) is assumed knowledge.
- **APIs.** REST over HTTP, sometimes gRPC, and FIX for anything order-related.
- **Scheduling.** Autosys, Control-M or Airflow driving the overnight batch.
- **CI/CD.** Jenkins or GitLab CI, Maven or Gradle, artefacts in Nexus or Artifactory.
- **Source control.** Git, with a branching model and mandatory code review. Bitbucket and GitLab are common in banks.
- **Observability.** Splunk or ELK for logs, Grafana dashboards, AppDynamics or Dynatrace for APM.
- **Containers.** Docker and Kubernetes/OpenShift where the platform team has got there; plenty of applications still run on VMs.

## What is different about writing code in a bank

This is the part candidates underestimate, and interviewers notice.

- **Change control is real.** You cannot push to production because your change is ready. There is a change record, an approval, a window, and a back-out plan. Emergency changes exist but they are audited.
- **The four-eyes principle.** Nothing significant goes in on one person's say-so. Code review is a control, not a courtesy.
- **Segregation of duties.** In many teams, developers do not have write access to production. You will diagnose a problem you cannot personally fix, and hand it to someone who can.
- **Audit and traceability.** Who changed what, when, and under which approved change. Logs may need to be retained for years.
- **Data sensitivity.** Client data, positions and P&L are confidential. Copying production data to a test environment is usually forbidden or requires masking. Do not put real data in a ticket, a screenshot or a chat message.
- **Backwards compatibility.** Other teams consume your interfaces. You cannot rename a field because you prefer a different name.

## Technical questions to be ready for

**Core language (assume Java unless told otherwise)**

- Difference between an interface and an abstract class, and when you would use each.
- `==` versus `.equals()`, and the contract between `equals()` and `hashCode()`.
- What happens when a `HashMap` has a bad hash function.
- Checked versus unchecked exceptions and how you decide.
- What `final`, `static` and `volatile` actually do.
- Threads: what a race condition is, what `synchronized` gives you, why you would use an `ExecutorService` rather than creating threads.
- Garbage collection at a high level — you do not need to name every collector, but you should know what a memory leak looks like in a managed language (something is still holding a reference).

**SQL — expect to be tested properly**

- Write a join across three tables.
- The difference between `INNER`, `LEFT` and `FULL OUTER` join, with an example of when a `LEFT` join changes the answer.
- `GROUP BY` with `HAVING` versus filtering in `WHERE`.
- Window functions: `ROW_NUMBER()`, `RANK()`, `LAG()` — very common for "find the latest record per instrument".
- Why a query is slow: missing index, a function applied to an indexed column, an implicit type conversion, or stale statistics.
- What a transaction is, and what isolation levels protect you from.

**Design and practice**

- How you would test the change you just described.
- The difference between a unit test and an integration test, and what you would mock.
- How you would make an operation idempotent — genuinely important when a message can be redelivered.
- Where you would put a cache, and how you would invalidate it.
- How you would handle a downstream service being down: retry, back off, dead-letter, or fail fast?

## What separates a strong candidate

Business context. Two candidates can both write the same correct SQL; the one who says *"this is a positions table, so I would expect one row per book per instrument per date, and if I am getting duplicates that probably means an amended trade was inserted rather than updated"* is the one who gets the offer.

Prepare one change you made end to end and be able to talk about it at three levels of depth: what it did for the business, how it worked technically, and what you would do differently now.

## Questions worth asking your interviewer

- What does the team's release cadence look like, and who runs the release?
- How much of the codebase is under test?
- Where does this application sit in the trade lifecycle, and who are the consumers downstream?
- What is the most common reason this system gets paged out of hours?
