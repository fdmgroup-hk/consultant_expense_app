# Order Flow, Venues and the FIX Protocol

Where the trade lifecycle explains *what happens to a trade*, order flow explains *how the instruction actually travels*. Interviewers use this topic to separate candidates who have read a glossary from candidates who can picture the plumbing.

## The path of an order

```
Client
  │  (FIX NewOrderSingle, or a phone call, or a click on a portal)
  ▼
Client connectivity / FIX gateway
  │  validation, normalisation, entitlement checks
  ▼
OMS  ── holds order state, compliance and limit checks
  │
  ▼
EMS / algo container  ── decides how to work the order
  │
  ▼
Smart Order Router  ── splits across venues
  │
  ▼
Venue (exchange, MTF, dark pool, SI, or a bilateral counterparty)
  │  (FIX ExecutionReport per fill)
  ▼
Fills flow back up: EMS → OMS → client
  │
  ▼
Trade capture → books and records → settlement
```

Two things to hold onto:

1. **Orders and trades are different objects.** An order has a lifecycle of its own (new → partially filled → filled, or cancelled, or rejected, or expired). A trade is the record of one execution. The relationship is one-to-many.
2. **Every hop is a place where a message can be lost, duplicated, delayed or malformed.** That is why FIX sequence numbers and session management exist, and it is why "the client says they sent an order and we have no record of it" is such a common support ticket.

## Order types you should be able to define

| Order type | What it means |
|---|---|
| **Market** | Execute immediately at the best available price. No price protection. |
| **Limit** | Execute only at the specified price or better. May not fill at all. |
| **Stop / stop-loss** | Becomes a market order once the stop price trades. Used to cap losses. |
| **Stop-limit** | Becomes a limit order once the stop price trades. |
| **IOC (Immediate Or Cancel)** | Fill whatever you can right now, cancel the rest. |
| **FOK (Fill Or Kill)** | Fill the whole thing right now or cancel entirely. |
| **GTC (Good Till Cancelled)** | Stays live across sessions until filled or pulled. |
| **Day** | Expires at the end of the trading session. |
| **Iceberg** | Only a small portion is visible on the book; the rest is hidden. |
| **MOC / LOC** | Market or limit on close — participates in the closing auction. |

## Venue types

- **Lit exchange** — the order book is public. LSE, NYSE, Nasdaq, HKEX, Xetra.
- **Dark pool** — no pre-trade transparency; used to move size without signalling. Trades print after the fact.
- **MTF (Multilateral Trading Facility)** — the EU/UK category for a non-exchange multilateral venue.
- **SI (Systematic Internaliser)** — a bank dealing on its own account against client flow, on a bilateral basis.
- **OTC** — bilateral, negotiated, no venue. Common for swaps, bonds and FX.

## Market microstructure basics

- **The order book** shows resting bids and offers by price level. **Depth** is how much is available at each level.
- **The spread** is the difference between the best bid and the best offer. It is the immediate cost of trading.
- **Price-time priority** is how most lit books allocate: best price first, and within a price level, whoever got there first.
- **Auctions** open and close the trading day. A large share of daily volume prints in the closing auction.
- **Market impact** is the price movement your own order causes. Working a large order slowly reduces impact but increases the risk that the price moves away from you — that trade-off is exactly what execution algorithms manage.

## FIX in practical terms

FIX (Financial Information eXchange) is a tag-value messaging protocol. A message is a series of `tag=value` pairs separated by the SOH character (ASCII 0x01), usually shown as `|` when logged.

### The message types you will actually see

| MsgType (tag 35) | Name | Purpose |
|---|---|---|
| `D` | NewOrderSingle | Place an order |
| `F` | OrderCancelRequest | Cancel an order |
| `G` | OrderCancelReplaceRequest | Amend an order |
| `8` | ExecutionReport | Every order state change and every fill |
| `9` | OrderCancelReject | Your cancel was refused |
| `A` | Logon | Start a session |
| `0` | Heartbeat | Keep the session alive |
| `1` / `2` | TestRequest / ResendRequest | Session recovery |
| `3` | Reject | Session-level rejection (malformed message) |

### Tags worth memorising

| Tag | Name | Notes |
|---|---|---|
| 35 | MsgType | What kind of message this is |
| 11 | ClOrdID | The client's own order id — your key for tracing |
| 37 | OrderID | The venue's or broker's order id |
| 55 | Symbol | The instrument |
| 54 | Side | 1 = Buy, 2 = Sell, 5 = Sell short |
| 38 | OrderQty | Quantity ordered |
| 40 | OrdType | 1 = Market, 2 = Limit, 3 = Stop, 4 = Stop limit |
| 44 | Price | Limit price |
| 59 | TimeInForce | 0 = Day, 1 = GTC, 3 = IOC, 4 = FOK |
| 39 | OrdStatus | 0 = New, 1 = Partially filled, 2 = Filled, 4 = Cancelled, 8 = Rejected |
| 150 | ExecType | What this ExecutionReport is telling you |
| 14 | CumQty | Cumulative quantity filled so far |
| 151 | LeavesQty | Quantity still working |
| 6 | AvgPx | Average fill price so far |
| 34 | MsgSeqNum | Session sequence number |
| 49 / 56 | SenderCompID / TargetCompID | Who is talking to whom |

A partial fill is not an error state: `OrdStatus=1`, with `CumQty` and `LeavesQty` adding up to `OrderQty`.

### Session vs application layer

FIX has two layers, and confusing them is a classic interview stumble.

- **Session layer** — logon, heartbeats, sequence numbers, resend requests. This is about the *connection*. A sequence-number mismatch is a session problem.
- **Application layer** — orders, executions, cancels. This is about the *business*.

A `Reject (35=3)` is a session-level rejection: the message was malformed. A `BusinessMessageReject (35=j)` or an `ExecutionReport` with `OrdStatus=8` is an application-level rejection: the message was well formed but the business logic refused it.

### What actually goes wrong with FIX in production

This is the part support teams get asked about, and it is worth having a real answer ready:

- **Sequence number mismatch after a disconnect.** The two sides disagree on where they were. Resolution is either a ResendRequest or, at start of day, a sequence reset agreed with the counterparty.
- **The session will not log on.** Wrong CompIDs, an IP not whitelisted, a certificate expired, or connecting outside the venue's session window.
- **Stale or missing heartbeats** causing the venue to drop the session.
- **Clock drift.** SendingTime (tag 52) outside the venue's tolerance gets messages rejected. Under MiFID II, clock synchronisation is itself a regulatory requirement.
- **Duplicate ClOrdIDs** after a client's own restart, causing rejects.
- **A fill arriving for an order the OMS thinks is already terminal**, usually a race after a cancel.

## A question you will probably be asked

> "Walk me through what happens when a client sends an order to buy 100,000 shares."

A strong answer covers: the order arrives over FIX and is validated; entitlement and limit checks run; the OMS creates the order and gives it a state; because 100,000 shares is large relative to average daily volume, it is worked by an algorithm rather than sent as one market order; the SOR slices it across venues; each fill comes back as an ExecutionReport, and the order goes partially filled with CumQty rising and LeavesQty falling; when it is complete the trade is booked, enriched with reference data, confirmed with the counterparty, cleared and then settled on the market's settlement cycle.

Then add the bit that shows judgement: **why** it is worked rather than sent at once — market impact — and **what you would check first** if the client called to say fills had stopped arriving.
