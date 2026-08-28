# Workflow: troubleshooting

Read the whole error before doing anything - every tool here is written to
say what broke and what to do about it. If you fix something not covered
below, add it here.

## `make doctor` shows a FAIL

Each `FAIL` line has a `->` fix hint right under it. Common ones:

- **`hotel identity`: name is still 'Hotel Aurora'.** Expected on a fresh
  clone. Edit `config/hotel.yaml`.
- **`routine orders`: autonomy is 'send' but ... is empty.** Name at least
  one supplier in `procurement.routine_orders.suppliers`, or set autonomy
  back to `draft`.
- **`llm provider`: claude-code selected but `claude` is not on PATH.**
  Install Claude Code, or switch `llm.provider` to `interactive` or
  `anthropic` in `config/hotel.yaml`.
- **`llm provider`: ANTHROPIC_API_KEY is not set.** Add it to `.env`, or
  switch `llm.provider` to `claude-code` or `interactive`.
- **An adapter shows FAIL, not warn.** `universal`/`built` adapters fail
  loud when misconfigured (a `warn` is reserved for stubs). Read the
  `detail` column - it names the missing file or variable.

## `make demo` does not print `DEMO OK`

- Make sure `make setup` ran first (`.venv` must exist).
- `tools/demo.py` forces `llm.provider=mock`, `mode=shadow` and every
  adapter to `mock`, and reads `fixtures/hotel/{reservations,covers,
  supply_items,waste_log}.json` from a fixed `--as-of` date - if you
  deleted or renamed those files, restore them from git.
- Read the traceback if there is one; `tools/demo.py` does not swallow
  errors on purpose, so a fixture problem shows up immediately.

## `make run` exits with code 3

Not an error. `llm.provider: interactive` parked a prompt. Read
`data/pending/*.prompt.md`, write your answer to the matching
`*.answer.json` (JSON only, matching the schema shown, no prose, no code
fence), and run the same command again. If there are two prompts for the
same order (`price-flag` then `order-message`), answer the one shown first
and re-run - answering both at once before re-running does not help, the
agent only looks for the answer to the stage it is currently on.

## `python3 tools/review.py send` did nothing

If `mode` is `shadow`, that is correct - it prints an explanation and sends
nothing, even for an order you just approved. See `docs/safety.md` and
`workflows/90-go-live.md`.

## An order is stuck at `sending`

A process died between claiming an order and finishing the send.
`tools/run.py` calls `core.store.Store.reap_stuck_sending()` on every pass,
which moves anything stuck for more than 30 minutes to `failed` so you see
it in the queue instead of it vanishing. Use
`python3 tools/review.py retry <id>` once the cause is fixed.

## A quantity looks wrong

There is no way to edit it directly (spec section 4, "no edit-quantity
control" - see `workflows/80-review.md`). Read the line's `reason` first -
it shows the exact arithmetic. The usual cause is a wrong fact in the
`supply_items` catalogue (par level, on-hand, daily use per occupied room)
or a config knob in `config/agent.yaml`'s `procurement:` block. Fix that,
then re-run - the next pass argues the number again from scratch.

## `python3 tools/*.py` says `ModuleNotFoundError: No module named 'core'`

You ran it with a Python that is not the repo's virtualenv, or from outside
the repo root. Use `make run` / `make doctor` / etc. (they call
`.venv/bin/python` for you), or run `.venv/bin/python tools/run.py` directly
from the repo root.

## Still stuck

`data/logs/*.jsonl` has every decision the agent made, in order, with a run
id, including the full thinking log for that run. `python3 tools/review.py
show <id>` has the full event trail for one order. If neither explains it,
that is a real bug - describe exactly what you ran and what you expected,
and ask.
