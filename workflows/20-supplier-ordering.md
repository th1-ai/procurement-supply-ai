# Workflow: Supplier Ordering AI ("The Buyer")

Objective: turn on the sub-agent that closes the loop from a decided order
to actually placing it, and understand exactly what changes.

Off by default. The parent (`workflows/10-procurement.md`) is fully useful
without this - every order still gets generated, argued and queued for a
human either way. See `docs/sub-agents.md` for the full picture.

## What it adds, once enabled

- A supplier in `subagents.supplier_ordering.portal_suppliers`
  (`config/agent.yaml`) gets a `channel: portal` label on its order, and
  once a human approves and sends it, `tools/review.py send` logs that
  clearly to `data/exports/procurement_orders.csv`. There is no generic
  online-portal API to build against (every supplier's is different) - see
  `docs/integrations.md`'s Procurement entry for the stub recipe.
- A supplier NOT in that list gets an LLM-drafted, ready-to-send message
  (`prompts/order-message.md`) attached to the order as its draft, so the
  human reviewing it has something to copy into WhatsApp or email instead
  of writing it from scratch.
- Neither of these skips human approval on its own. The parent's separate
  routine-order lane (`procurement.routine_orders` in `config/agent.yaml`)
  is the only path that can send with nobody reviewing first, and that is
  opt-in twice over - see `docs/how-it-works.md` design decision 6.

## Steps

1. **Turn it on.**
   ```yaml
   # config/agent.yaml
   subagents:
     supplier_ordering:
       enabled: true
       portal_suppliers: ["Linens & Co", "Green Grocer Lda", "Office & Guest Supplies"]
   ```
   Fill `portal_suppliers` from `knowledge/suppliers.md` - only list a
   supplier here if they genuinely have an online ordering system you would
   place the order through.

2. **Run the loop.**
   ```bash
   make run
   ```
   A no-portal supplier's order now pends an extra `order-message` prompt
   (or, on `llm.provider: mock`, drafts it immediately from
   `fixtures/expected/order-message/`). Read `workflows/10-procurement.md`
   step 2 for how a price-flagged AND no-portal order can pend twice on the
   SAME order - answer both, in order.

3. **Review and edit the message.** `make review` shows the order as
   before. `python3 tools/review.py show <id>` includes the drafted message
   under `draft`. If it needs a rewrite:
   ```bash
   python3 tools/review.py edit <id> --message-file my-version.txt
   ```
   This only replaces the message text - there is still no way to edit a
   line's quantity (see `workflows/80-review.md`).

4. **Send it.** `python3 tools/review.py send` (once `mode: live` and the
   order is approved) appends the audit line and, if the order carries a
   drafted message, sends it through the adapter that matches the
   message's `channel`. `whatsapp` goes through `systems.messaging` to the
   chat id in `procurement.supplier_chat_ids` (`config/agent.yaml`);
   `email` goes through `systems.email` to the address in
   `procurement.supplier_emails` - but only once `systems.email.adapter`
   is a real adapter (`imap` or `gmail`). Set both blocks for every
   no-portal supplier before going live with this sub-agent - they are
   blank on the shipped example. Left unconfigured, an `email`-channel
   order is refused with the approval kept, not silently sent over
   WhatsApp - see `docs/integrations.md`.

## What does not happen (spec's open question, still open)

There is no per-supplier "has a portal" branch that actually calls a real
API - every supplier's ordering system is different, and this repo does not
invent one. `channel: portal` is a label and an audit line, not an
integration. See `specs/supplier-ordering-ai.md` section 11 and
`docs/integrations.md`.
