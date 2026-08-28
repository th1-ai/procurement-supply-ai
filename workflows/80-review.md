# Workflow: working the review queue

Objective: turn a queued order into a decision - approve, edit, or reject -
and, once approved, actually send it.

**Nothing leaves the building without going through this, and nothing
leaves in `mode: shadow` even once approved.** `core.review.evaluate_write`
blocks every write while shadow is on; approving or editing an order only
records your decision for the record and to teach the agent - it does not
send anything until `mode: live`. See `docs/safety.md`.

## Steps

1. **See what is waiting.**
   ```bash
   make review
   ```
   Each line shows the order id, its status (`pending_review` or
   `needs_human`), the supplier, the total, and whether it carries a price
   flag.

2. **Read one in full.**
   ```bash
   python3 tools/review.py show <id>
   ```
   This prints every line's quantity with its arithmetic reason, the
   price-flag note if there is one, the drafted supplier message if the
   Supplier Ordering sub-agent wrote one, and the full event history.
   Summarise it in plain language - which supplier, how much, why that
   quantity, and whether anything is flagged - do not paste the raw JSON at
   the person running this.

3. **Decide.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py edit <id> --message-file my-version.txt
   python3 tools/review.py reject <id> --reason "price too high, calling around instead"
   ```
   There is deliberately no way to edit a line's quantity - every quantity
   already carries its own justification. If a number looks wrong, that
   means a config knob or a catalogue fact is wrong (par level, on-hand,
   daily use) - fix that in `config/agent.yaml` or the `supply_items` table
   and re-run, rather than hand-editing a number the agent already argued
   for (spec section 4, "no edit-quantity control"). `edit` only replaces
   the drafted outbound message text, and only exists on orders that have
   one (Supplier Ordering sub-agent, no-portal supplier).

4. **Send what was approved.** Only once `mode: live`:
   ```bash
   python3 tools/review.py send
   ```
   This claims everything `approved`/`edited`, logs the order to
   `data/exports/procurement_orders.csv`, and - if it carries a drafted
   message - sends it through the adapter that matches the message's
   `channel`: `systems.messaging` for `whatsapp`, `systems.email` for
   `email` (only once you have configured a real email adapter and the
   supplier's address - `docs/integrations.md`). In `mode: shadow`, `send`
   prints a short explanation and does nothing at all; there is no
   exception for an order you just approved (see `docs/safety.md`).

5. **A blocked or failed send.** A configuration problem - `mode: shadow`,
   or an `email`-channel order with no real `systems.email` adapter or no
   address for that supplier - prints `blocked <id> (approval kept): ...`
   and leaves the order `approved`; just fix the cause and run `send`
   again, no `retry` needed. An actual send error (a bad credential, the
   adapter unreachable) marks the order `failed` with the error attached:
   ```bash
   python3 tools/review.py retry <id>
   ```
   re-queues it for another attempt after you have fixed the cause (usually
   a messaging credential - `make doctor` will say which).

6. **A delivery arrives.**
   ```bash
   python3 tools/review.py deliver <id>
   ```
   Only works on an order that is `sent` or `auto_sent`. Increments
   `on_hand` per line in the `supply_items` table and marks the order
   delivered - the next run's forecast sees the new stock level.

## Rules

- Only `tools/review.py` writes `approved` / `edited` / `rejected`.
- A price-flagged order is always `needs_human` - never approve one without
  reading the flag note and checking with the supplier if it looks wrong.
- Confirm with the person running this before switching to `mode: live`,
  even once the queue has been exercised for a while -
  `workflows/90-go-live.md` covers when and how.
