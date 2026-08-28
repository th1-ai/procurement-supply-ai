# Workflow: the weekly ordering loop

Objective: run one pass, see the orders the Quartermaster argued from the
forecast, and understand what each line's reasoning means.

## Inputs

- A configured `systems.pms.adapter` (`mock` by default - see
  `workflows/00-setup.md` step 6 to connect a real occupancy feed).
- `fixtures/hotel/covers.json` (or your own covers file - restaurant
  bookings have no adapter, see `docs/integrations.md`).
- Your own catalogue in the `supply_items` table (seeded once from
  `fixtures/hotel/supply_items.json` - see `workflows/00-setup.md` step 4).
- `config/agent.yaml`'s `procurement.*` knobs - the defaults match the spec;
  change one and re-run to see the effect (`docs/how-it-works.md`).

## Steps

1. **Run one pass.**
   ```bash
   make run
   make run ARGS="--limit 5"                  # just the first five orders
   make run ARGS="--dry-run"                  # compute and print, write nothing
   make run ARGS="--as-of 2026-09-01"         # forecast from a fixed date
   ```
   For each supplier with at least one non-zero line, the engine
   (`tools/supply_engine.py`) builds one draft order, computes every line's
   quantity from occupancy and covers, and writes the arithmetic into that
   line's `reason`. Any order with a line more than 8% above its 90-day
   baseline gets an LLM-drafted `price-flag` note
   (`prompts/price-flag.md`) and goes to `needs_human`; everything else
   goes to `pending_review`. One run-level `purchase-note` narrates the
   whole pass at the end - cosmetic, never gates anything.

2. **If `llm.provider` is `interactive`,** the run stops with exit code 3
   and parks a prompt in `data/pending/`. Read `*.prompt.md`, write your
   answer as JSON to the matching `*.answer.json` exactly matching the
   schema shown, and run the same command again. An order needing a
   price-flag note AND (Supplier Ordering sub-agent only) a drafted message
   can pend twice in a row on the SAME order - answer each prompt and
   re-run; the agent resumes at whichever stage is still open, it never
   re-asks a question you already answered or skips the order (see
   `docs/how-it-works.md` "Idempotency").

3. **See what happened.**
   ```bash
   make review
   ```
   `workflows/80-review.md` covers approve / edit / reject / send in full.

4. **Keep it running.**
   ```bash
   make watch
   ```
   Or schedule it - `make schedule` and `scheduler/` have cron, launchd and
   systemd examples, generated from `config/agent.yaml`'s `schedule:` block.
   This repo ships one job, `procurement`, at `morning` cadence (07:00
   daily) - the horizon is a week, so a daily check catches occupancy and
   covers that changed since yesterday.

## Edge cases

- **A zero-guest week.** Every room-driven and covers-driven line's basis
  falls to zero, so nothing clears its on-hand and no order is generated -
  see `specs/procurement-supply-ai.md` section 6 and
  `fixtures/inbound/week-zero-occupancy.json`. This is not a special-cased
  check; it falls straight out of the formula.
- **A perishable at its cap.** `docs/how-it-works.md`'s mermaid diagram
  shows where the waste guard sits - it caps a perishable line (F&B,
  1-day-or-less lead time) at 105% of forecast even if the par level would
  order more. The trimmed amount is logged in the run's thinking log.
- **A re-run of the same week.** `external_id` is
  `<week_start>:<supplier-slug>`; re-running is a no-op for any order that
  has already left `new` (see `docs/how-it-works.md` "Idempotency" for why
  the payload of an already-sent order is never silently rewritten by a
  later run).
- **A model answers off-schema.** `core.llm` raises `LLMSchemaError` rather
  than accept a bad answer; the order is queued as `needs_human` with the
  error recorded, instead of guessing a price-flag note or a supplier
  message.
