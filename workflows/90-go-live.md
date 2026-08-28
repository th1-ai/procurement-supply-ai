# Workflow: shadow to live

Objective: decide, together with the person running this, whether the
Quartermaster is ready to send approved orders on its own instead of only
drafting them - and make the change safely if so.

This is their decision, never the agent's. Do not suggest it until the
checklist below is genuinely true, and when you do raise it, say plainly
what changes: **approved orders start actually sending; nothing that was
never approved ever does.**

## Checklist

- [ ] `make doctor` is clean (no `FAIL` lines). `warn` on `mode` is expected
      until you flip it.
- [ ] `config/hotel.yaml` has the real property name, address and room
      count, and the `supply_items` catalogue is the property's own, not
      the shipped 16-SKU sample (`workflows/00-setup.md` step 4).
- [ ] At least a couple of weeks of real `make run` passes have gone
      through the review queue, not just the demo fixtures - long enough to
      see the catalogue's par levels and daily-use rates are realistic.
- [ ] A price-flagged order has come through at least once and been checked
      by a person, so you know the 8% threshold
      (`procurement.price_watch_threshold_pct`) is set sensibly for this
      property's suppliers.
- [ ] If turning on the Supplier Ordering sub-agent
      (`workflows/20-supplier-ordering.md`): `procurement.supplier_chat_ids`
      has a real chat id for every no-portal supplier, and at least one
      drafted message has been read and approved by a person.
- [ ] Run the go-live sweep - the shadow-era queue was drafted before you
      trusted the numbers and is now out of date:
      ```bash
      python3 tools/review.py stale
      ```

## Making the change

1. Edit `config/hotel.yaml`:
   ```yaml
   mode: live
   ```
2. `review.require_approval_for` still lists `send_message` and `pms_write`
   by default - it should. Going live means **approved orders get sent**,
   not that the agent starts sending unapproved ones. There is no config
   that removes the approval step for a normal order.
3. Run `make doctor` again to confirm.
4. Run one real pass and manually watch a send go through:
   ```bash
   make run ARGS="--limit 1"
   python3 tools/review.py list
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
5. Tell the person running this exactly what just changed: an approved
   order now actually leaves the next time someone (or the scheduled job)
   runs `python3 tools/review.py send` - it is still never automatic before
   that approval.

## The one path that IS automatic, and how to turn it on deliberately

`procurement.routine_orders` (`config/agent.yaml`) can send a small,
low-risk, un-flagged order to a named supplier (the roster's "daily bakery
order") with nobody reviewing that particular one first. It stays inert
until **three** things are all true - see `docs/how-it-works.md` design
decision 6:

1. `procurement.routine_orders.autonomy: send` (default `draft`).
2. `mode: live`.
3. `send_message` removed from `review.require_approval_for` in
   `config/hotel.yaml` - do this only after watching that supplier's drafts
   in the review queue for a while.

Until all three are true, the attempt is caught and the order falls back to
`pending_review` like any other - nothing breaks, it just is not automatic
yet.

## Going back to shadow

```yaml
mode: shadow
```
in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env` for one run. Either
stops every outbound action on the next pass, mid-schedule, with no other
change required.
