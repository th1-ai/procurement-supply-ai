# Sub-agents in this repo

Procurement / Supply AI ships one sub-agent, folded into the same loop, the
same tables and the same review queue as the parent - see
`docs/how-it-works.md` for the shared data model.

## Supplier Ordering AI — "The Buyer"

**Off by default.** The parent is fully useful without it: every order
still gets generated, argued and queued for a human, and `tools/review.py
send` still logs the audit line and sends any drafted message regardless
of whether this sub-agent ever ran. Turn it on with
`workflows/20-supplier-ordering.md`.

**Does** (roster, verbatim): "Where a local supplier has an online
ordering portal, it places the F&B order directly, matching quantities to
occupancy and par levels, and schedules the delivery. No phone calls, no
manual basket."

**Won't** (roster, verbatim): "Needs a supplier with an online ordering
system; where there isn't one, it falls back to a drafted order for a
human to place."

**Why** (roster, verbatim): "Closes the loop the Procurement AI starts,
going from what to order to actually placing and scheduling it."

**Output** (roster, verbatim): "Hands-free F&B replenishment matched to
real occupancy."

### What it actually adds, honestly

There is no generic online-ordering-portal API to build a universal
integration against - every supplier's system is different, and this
family does not fake one (`docs/integrations.md`'s Procurement entry).
What this sub-agent adds when enabled:

1. **A portal label and an audit line.** A supplier listed in
   `subagents.supplier_ordering.portal_suppliers` (`config/agent.yaml`)
   gets its approved order logged as `channel: portal` in
   `data/exports/procurement_orders.csv` - a clear record of what was
   meant to go to their system. Actually placing it there is a hotel's own
   click, or the recipe in `docs/integrations.md#implement-your-own` for
   that one supplier's specific API.
2. **A drafted message for everyone else.** A supplier NOT in that list
   gets an LLM-drafted, ready-to-send order message
   (`prompts/order-message.md`) attached to the order's `draft`, with a
   `channel` of `whatsapp` or `email`. A human still reads it before it
   sends (`workflows/80-review.md`). `tools/review.py send` delivers a
   `whatsapp` message through `systems.messaging`; an `email` message only
   actually sends through `systems.email` once you have configured a real
   adapter (`imap` or `gmail`) and the supplier's address - otherwise
   `send` refuses it, approval kept, rather than send it over WhatsApp
   instead. See `docs/integrations.md`.

Neither of these skips approval. The parent's separate, narrower routine-
order lane is the only path in this whole repo that can send with nobody
reviewing it first, and it is opt-in three times over regardless of
whether this sub-agent is on - see `docs/how-it-works.md` design
decision 6.

### Fixtures and tests

- `fixtures/expected/order-message/2026-09-01__frigo-atlantico.json` - the
  mock answer for the demo's flagged, no-portal order.
- `tests/test_procurement_subagent.py` - `needs_order_message`,
  `channel_for`, and a mock-provider draft.
- `tests/test_procurement_retry.py` - the interactive-provider case where
  an order needs BOTH a `price-flag` note (parent) and an `order-message`
  draft (this sub-agent) before it can leave `new`.

### Enabling it

```yaml
# config/agent.yaml
subagents:
  supplier_ordering:
    enabled: true
    portal_suppliers: ["Linens & Co", "Green Grocer Lda", "Office & Guest Supplies"]
```

And set a real WhatsApp chat id per no-portal supplier in
`procurement.supplier_chat_ids` before relying on `tools/review.py send`
to actually deliver a drafted message.
