# Measuring the benefit

## The business case

**Argues every order from the forecast, not habit. It reads the coming
nights of occupancy and the covers already on the restaurant book — the
same forecast the pricing engine prices against — applies per-occupied-room
consumption rates and supplier lead times to every stock line, and
generates the orders with the reasoning on each line. It watches days of
cover against par, caps perishables at forecast so waste can't creep back
in, flags supplier price creep against the 90-day average, and sends
routine orders to suppliers automatically (e.g. the daily bakery order over
WhatsApp).**

**Why it matters.** Eliminates over/under-ordering and the daily "did
anyone call the baker?" Cost discipline on autopilot.

**What to expect.** Every order sized to real occupancy with the reason on
the line; waste write-offs bend down the week it switches on.

**ROI (from the roster).** −12% F&B over-ordering & waste (labor).

## What to measure

```bash
make report
```

Five numbers, each tied to the claims above:

1. **Orders and their status.** How many supplier orders are in the store
   right now, by supplier and by review status. A healthy queue has
   orders moving from `pending_review`/`needs_human` to `sent` within a
   day or two of the weekly run - a growing backlog of un-reviewed orders
   is worth a conversation, not a config change.
2. **Order value vs. a flat top-up.** `spend_vs_naive` compares what was
   actually ordered against `supply_engine.naive_total` - a par-level
   top-up ignoring occupancy entirely. The roster's "over/under-ordering"
   claim is this delta, printed on every run's decision line.
3. **Price flags.** How many orders needed a price-creep check before
   approval. Zero forever probably means the 8% threshold
   (`procurement.price_watch_threshold_pct`) is set too high for how your
   suppliers actually behave - watch this number, not just the flag
   itself.
4. **Edit rate.** Of every order a human approved or edited, how often
   they rewrote the drafted supplier message (Supplier Ordering sub-agent
   only - plain orders have nothing to edit, by design). A high rate
   against one supplier usually means `prompts/order-message.md` needs a
   clearer instruction, or that supplier's entry in `knowledge/suppliers.md`
   is thin.
5. **Waste trend.** `waste_log`'s before/after split at the day the AI took
   the order book - the roster's headline claim, "waste write-offs bend
   down the week it switches on" - is this number, computed the same way
   every time by `supply_engine.summarise_waste`.

## The honest caveats

- **The waste trend needs a real "before" period.** On a fresh clone, the
  sample `fixtures/hotel/waste_log.json` shows what the demo's invented
  property looked like - it is not your data. Track your own actual waste
  write-offs for a few weeks before switching this agent on, and after, to
  get a real before/after.
- **The catalogue is the whole ballgame.** Every number in this report is
  only as good as `supply_items`' par levels, on-hand accuracy and daily-use
  rates. A stale on-hand figure (nobody counted stock this week) produces a
  confidently wrong order with a perfectly reasonable-looking `reason`
  string. Keep on-hand current, ideally via `tools/review.py deliver`
  every time a delivery actually arrives.
- **Price flags depend on having 90 days of real baseline data.** The
  shipped catalogue's `baseline_unit_cost` is invented for the demo. Once
  you have real invoices, keep this field current (manually, or by asking
  your Claude Code session to update it from your accounting export) - an
  unmaintained baseline either never flags a real price hike or flags one
  that has long since become the new normal price.
- **`−12%` is the roster's figure for the family this agent is drawn from,
  not a guarantee for your property.** How close you get depends entirely
  on how good the catalogue is, and how quickly a human works the review
  queue rather than letting orders stack up.
