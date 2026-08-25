# The Trade Lifecycle, End to End

This is the single most useful thing to understand before a placement on a bank technology team. Almost every system you touch sits at one stage of this chain, and almost every incident is a break somewhere along it. If you can explain where your application sits in this flow, you are ahead of most graduate candidates.

## The one-sentence version

A client decides to buy or sell, the order is routed to a venue and executed, the resulting trade is booked and enriched with reference data, both sides confirm they agree on the terms, the trade is cleared, cash and securities change hands on settlement date, and then it is reconciled, risk-managed and reported to regulators.

## Stage 1 — Pre-trade

Before anything is ordered, the bank works out what it is willing to do.

- **Research and idea generation.** Analysts publish views; the sales desk takes them to clients.
- **Pricing and quoting.** For exchange-traded instruments the price comes from the market. For OTC products the desk prices it from a model, and the client may send an RFQ (Request For Quote) to several banks at once.
- **Pre-trade risk and limit checks.** Does this client have credit headroom? Does this breach a position limit, a concentration limit, or a restricted-list rule? These checks are automated and they must be fast — a slow limit check delays the order.
- **Suitability and compliance checks.** Is this product appropriate for this client's classification? Is anyone on the desk in possession of inside information about the name?

**Technology at this stage:** pricing engines, quote distribution, limit-check services, client-onboarding and KYC data, restricted-list feeds.

## Stage 2 — Order placement and routing

The order enters the bank's systems.

- **OMS (Order Management System)** holds the order, its state, and its relationship to the client. It is the book of record for orders.
- **EMS (Execution Management System)** decides how to execute: which venue, what algorithm, how to slice a large order so it does not move the market.
- **Smart Order Routing (SOR)** splits an order across venues chasing the best available price and liquidity.
- **Algos** — VWAP, TWAP, Implementation Shortfall, POV (Percentage of Volume) — automate the slicing over time.
- **FIX protocol** is the messaging standard that carries orders and executions between the bank, its clients and the venues.

An order is not a trade. One order can produce many executions (fills), and a partially filled order is a normal, expected state.

## Stage 3 — Execution

The order meets a counterparty at a venue: a lit exchange, a dark pool, an MTF/SI in Europe, or bilaterally over the counter. The venue sends back **execution reports** — each one a fill with a price and quantity. The average of the fills becomes the order's average execution price.

**Best execution** is a regulatory obligation: the bank must be able to evidence it took reasonable steps to get the best result for the client, taking price, cost, speed and likelihood of settlement into account. This is why execution data is captured and stored so carefully.

## Stage 4 — Trade capture and booking

The execution becomes a **trade** in the bank's books and records.

- The trade is booked into a **trade capture system** against a specific **book** (a portfolio owned by a desk).
- It is **enriched** with reference data: the instrument's identifiers (ISIN, CUSIP, SEDOL, RIC), the legal entity of the counterparty (LEI), the settlement instructions (SSIs), the calendar and holiday rules that determine the settlement date.
- Fees, commissions and taxes are calculated (stamp duty in the UK, for example).

**This is where most breaks start.** A missing or stale reference-data record — an instrument that was not set up, a counterparty whose SSIs changed, a holiday calendar that was not updated — silently produces a trade that cannot settle. Production support teams spend more time on reference data than on anything else.

## Stage 5 — Confirmation and affirmation

Both sides check they agree on what was traded.

- **Confirmation** is the legal record of the trade's terms sent to the counterparty.
- **Affirmation** is the counterparty agreeing to them.
- Electronic matching platforms (DTCC's CTM, MarkitWire for derivatives) do this automatically; anything unmatched drops out for a human to chase.
- SWIFT messages carry much of this traffic: MT515/MT518 for securities confirmations, MT540–MT547 for settlement instructions.

An **unmatched** or **unaffirmed** trade is an early warning that it will fail to settle.

## Stage 6 — Clearing

Clearing sits between execution and settlement and is about managing the risk that someone does not pay.

- For exchange-traded and mandated OTC products, a **CCP (Central Counterparty)** steps into the middle through **novation**: the original bilateral trade becomes two trades, each facing the CCP. Now neither party carries the other's credit risk.
- The CCP takes **margin**: **initial margin** up front against potential future exposure, and **variation margin** daily against actual mark-to-market moves.
- **Netting** collapses many trades in the same instrument into a single obligation to deliver or receive, which dramatically reduces settlement traffic.

Examples: LCH, ICE Clear, Eurex Clearing, DTCC's NSCC for US equities, HKSCC for Hong Kong.

## Stage 7 — Settlement

Cash and securities actually change hands.

- **Settlement cycle.** US, Canadian and Mexican equities settle **T+1** (since May 2024). Hong Kong, the UK and the EU currently settle equities **T+2**, with the UK and EU committed to moving to T+1 in October 2027. FX spot is generally T+2 (USD/CAD is T+1). Knowing your market's cycle matters — a shorter cycle compresses the time available to fix a break.
- **DvP (Delivery versus Payment)** ensures the securities leg and the cash leg move together, so neither side is left exposed.
- The mechanics run through **custodians** (who hold assets for the bank or its clients) and **CSDs / ICSDs** (Euroclear, Clearstream, DTC, CCASS in Hong Kong) who maintain the definitive record of ownership.
- **Nostro and vostro** accounts are the cash accounts the bank holds with, and on behalf of, other banks. Reconciling them is a daily job.

### Settlement fails

A **fail** is when the trade does not settle on the intended date. Common causes:

- The seller does not have the securities (they were lent out, or an earlier trade in the chain failed).
- Wrong or missing SSIs.
- A mismatch in the trade details that was never resolved.
- Cut-off missed, or an unexpected market holiday.

Fails cost money. Under the EU's **CSDR** settlement discipline regime, cash penalties accrue daily on failing trades. Chasing fails is core operations work, and the systems that flag them are core production-support territory.

## Stage 8 — Post-settlement

- **Reconciliation.** The bank compares its own records against the custodian's, the CCP's, and the exchange's. Any difference is a **break** that must be investigated and cleared. Recs run overnight and the breaks are on someone's desk in the morning.
- **Position keeping and P&L.** Positions are marked to market; daily P&L is produced and explained. A **P&L break** — where the P&L the system produces does not match what the desk expects — is escalated fast, because it usually means a bad price, a bad position, or a mis-booked trade.
- **Corporate actions.** Dividends, stock splits, mergers, rights issues all change positions and need to be applied correctly, sometimes with an election from the holder.
- **Collateral management.** Margin calls are issued, met and disputed daily.

## Stage 9 — Reporting

- **Regulatory reporting.** MiFID II / MiFIR transaction reporting in the EU and UK, EMIR for derivatives, Dodd-Frank in the US, and local regimes such as the HKMA's and SFC's requirements in Hong Kong. Deadlines are typically T+1 and missing them is a reportable breach.
- **Risk reporting.** VaR, sensitivities (the Greeks), stress tests, limit utilisation.
- **Books and records / finance.** The general ledger, regulatory capital, and the daily close.

## How to use this in an interview

Do not recite the stages. Instead:

1. Name the stage your application sits at.
2. Explain what breaks there and what the consequence is downstream.
3. Give one concrete example.

**Weak answer:** "The trade lifecycle is pre-trade, execution, clearing and settlement."

**Strong answer:** "My application sat in trade capture, enriching executions with reference data before they went to the books-and-records system. The failure we saw most often was a new instrument that had not been set up in the reference-data master, so the trade would book but fail validation overnight. If we did not catch it before the recs ran, it turned into a settlement fail two days later and Ops would have to chase the counterparty — so the fix was a pre-booking check that flagged unknown ISINs at the point of capture rather than at end of day."

The second answer shows you understand the chain, not just the vocabulary.
