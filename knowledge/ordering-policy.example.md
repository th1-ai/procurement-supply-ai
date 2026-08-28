# Ordering policy - Hotel Aurora (example)

Copy this to `ordering-policy.md` and adjust the numbers to your own
property. This file is for people, not for the agent to read automatically -
the numbers that actually govern behaviour live in `config/agent.yaml`.

## What never changes, regardless of config

- No order is ever sent while `mode: shadow` (the default). See
  `docs/safety.md`.
- Perishables (F&B items with a lead time of 1 day or less) are never
  ordered above 105% of the forecast, even if the par level says otherwise.
  This is a hard cap in `tools/supply_engine.py`, not a suggestion.
- A supplier price more than 8% above its 90-day baseline is always flagged
  for a human, never silently paid.

## What a hotel decides

- **`procurement.routine_orders.suppliers`** - which suppliers are routine
  enough that, once you trust the pattern, their small daily orders can send
  with nobody reviewing them first. Start with none. Add one (the bakery is
  the obvious first candidate) only after a few weeks of watching its
  drafts in the review queue.
- **`procurement.routine_orders.max_total_eur`** - the ceiling below which a
  routine order is even eligible for that lane. Keep it low.
- **`subagents.supplier_ordering.portal_suppliers`** - which suppliers have
  an online ordering system you would actually place the order through.
  Keep `knowledge/suppliers.md` and this list in sync.
- **`procurement.covers_per_occupied_room`** - your own F&B attach rate.
  The shipped default (4.6) is the demo's constant, not your property's.
