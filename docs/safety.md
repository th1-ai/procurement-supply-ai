# Guardrails and safety

This agent decides what to order and can, in one narrow case, send a
message to a supplier on its own. Everything below is built in, not
optional, and this page explains what it does and what is left for the
person running it to decide.

## The two modes

| Mode | What happens |
|---|---|
| `shadow` (default) | The agent reads, forecasts, drafts and queues. It **never** sends a message, writes to a PMS, or logs an order as sent - not even for an order a human has approved. |
| `live` | Approved orders are really sent. Everything else still waits. |

`mode` lives in `config/hotel.yaml`. It is a global kill switch with **no
exception for an approved item** - approving or editing an order in shadow
mode only records the decision and teaches the agent; the write is still
blocked (`core/review.py`'s `evaluate_write`). Flipping `mode` back to
`shadow` stops every outbound action immediately, mid-schedule, with no
other change. `config/agent.yaml` can be stricter than `hotel.yaml`, never
looser.

Two more brakes:

- `make run ARGS="--dry-run"` computes the whole forecast and prints it,
  and writes nothing at all - not an item, not a seeded catalogue row, not
  a `runs` row, not a model call. Use it to preview a config change.
- `review.require_approval_for` in `config/hotel.yaml` lists the actions
  that need a human even in live mode. The defaults are `send_email`,
  `send_message`, `pms_write`, `payment`, `publish`. Shortening that list
  is how you hand the agent more rope, one action at a time - see
  `docs/how-it-works.md` design decision 6 for the one case this repo
  actually uses that for.

Every outbound action in the codebase goes through one function,
`core/review.py:assert_write_allowed`. There is no second path.

## The review queue

Nothing leaves the building without passing through the queue.

```bash
make review                                # what is waiting
python3 tools/review.py show <id>           # the full order and how it got there
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --message-file my-version.txt
python3 tools/review.py reject <id> --reason "price too high, calling around"
python3 tools/review.py send                # only sends once mode: live
python3 tools/review.py stale               # go-live step - see workflows/90-go-live.md
```

An order moves `new -> pending_review` (or `needs_human` if a price is
flagged) and then waits. Only `tools/review.py` can write `approved`,
`edited` or `rejected`; only `tools/run.py`/`tools/review.py send` can write
`sending`/`sent`/`auto_sent`. A crash between "claimed for send" and "sent"
is picked up on the next pass (`reap_stuck_sending`) and shown to you as
`failed` rather than silently retried or lost.

**No edit-quantity control, by design.** Every quantity already carries its
own justification in the order line's `reason`. If a number looks wrong,
the fix is a config knob or a catalogue fact, not a hand-edit - see
`workflows/80-review.md`.

## What the agent will not do

- Send or log anything as sent while `mode: shadow` - no exception, even
  for an approved order.
- Order on a zero-guest night. Every room- and covers-driven line's basis
  is zero when occupancy and covers are zero, so nothing clears its
  on-hand. This is a property of the formula, not a special-cased check -
  see `specs/procurement-supply-ai.md` section 6.
- Order a perishable above 105% of forecast, even when the par level would
  order more. `tools/supply_engine.py`'s waste guard is a hard cap that
  runs after the par-buffer cushion, not a suggestion.
- Silently pay a price hike. A line more than 8% above its 90-day baseline
  always gets a `price-flag` note and routes the whole order to
  `needs_human` - it is never averaged in or approved automatically.
- Send the one truly-automatic routine order to anyone but the named
  supplier(s) in `procurement.routine_orders.suppliers`, and only above
  `mode: live` plus the other opt-ins in `docs/how-it-works.md` design
  decision 6.
- Invent an item, a quantity, a price or a supplier name that is not in the
  data it was given. The three LLM tasks (`purchase-note`, `price-flag`,
  `order-message`) all say so explicitly in their prompts, and a
  schema-invalid answer is queued `needs_human` rather than guessed at.

## Data handling

**No guest data at all.** This agent never reads a guest's name, email or
any other personal detail - `tools/run.py::fetch_demand_inputs` pulls only
`check_in`, `check_out` and `status` from each reservation before anything
else touches it. What it does handle is supplier names, item names and
prices, none of it personal data under GDPR.

**What leaves your machine.** With `llm.provider: anthropic` or
`claude-code`, the prompt goes to Anthropic. That prompt contains order
lines, totals and supplier names - never a guest's details. With
`llm.provider: mock` or `interactive`, nothing leaves the machine at all.

**What is stored, and where.** Everything lives in `data/` inside this
folder: `agent.db` (SQLite - orders, the catalogue, the waste log),
`logs/*.jsonl`, `exports/procurement_orders.csv`. `data/` is gitignored.
There is no cloud service behind this repo and no telemetry.

**Retention.** `privacy.retention_days` (default 365) is how long processed
orders stay in the database. Deleting `data/agent.db` deletes everything
the agent knows, including the current stock levels - reseed the catalogue
from `fixtures/hotel/supply_items.json` (or your own file) afterwards.

## Telling a supplier they are talking to AI

The EU AI Act (Article 50) requires that a person is told when they are
interacting with an AI system, unless it is obvious. This agent's only
guest-adjacent text is the supplier-facing order message
(`prompts/order-message.md` and the routine-order template in
`tools/supply_engine.py::routine_order_message`) - both sign off with the
property's name. Add a short line to the message if your supplier
relationship is not already an obvious "this is an automated order system":

> This order was prepared automatically from our current bookings. Reply
> to this number any time to reach a person directly.

Keep the escape hatch in the sentence - a supplier who wants to speak to a
person should never have to work out how.

## Subscription or API: an honest note

Two ways to pay for the reasoning:

**Your Claude Code subscription** (`llm.provider: claude-code` or
`interactive`). Flat monthly cost, no per-message billing, and this
agent's three tasks are all short and infrequent (a handful of calls a
week) - genuinely the cheapest way to run this one.

The caveat, plainly: a personal Pro or Max subscription is intended for
interactive use, and Anthropic's usage policy and rate limits apply to
automated use of it. A weekly scheduled run is a normal way to work.

**The Anthropic API** (`llm.provider: anthropic`). Pay per token, no
ambiguity about automated use, proper rate limits, and usage you can
attribute. `make report` shows what you are spending.

Start on the subscription. There is little reason to move this
particular agent to the API given how few calls it makes a week, but the
option is there if you run many properties from one deployment.

## If something goes wrong

1. `mode: shadow` in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env`.
   Every outbound action stops on the next pass.
2. Remove the schedule (`crontab -e`, `launchctl unload`, or
   `systemctl disable --now <slug>.timer`).
3. `make doctor` to see what the agent thinks its state is.
4. `data/logs/*.jsonl` has every decision, with the run id and the full
   thinking log, in order.
