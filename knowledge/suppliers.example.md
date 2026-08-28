# Suppliers - Hotel Aurora (example)

Copy this to `suppliers.md` and replace every line with your own suppliers.
Keep the "portal" column honest - `config/agent.yaml`'s
`subagents.supplier_ordering.portal_suppliers` should list exactly the
suppliers marked "yes" here.

| Supplier | Supplies | Lead time | Online ordering | Contact |
|---|---|---|---|---|
| Frigo Atlântico | Fresh fish, seafood, meat | 1-2 days | No - phone or WhatsApp | +1 555 0201, orders@example.com |
| Lisbon Bakery Co. | Bread, pastries | Next day | No - WhatsApp | +1 555 0202 |
| Green Grocer Lda | Produce, dairy | 2 days | Yes - grocerlda.example.com/orders | orders@example.com |
| Linens & Co | Bed linen, towels, table linen | 3 days | Yes - linensco.example.com/b2b | account manager, +1 555 0204 |
| Office & Guest Supplies | Amenities, paper goods, cleaning supplies | 4-7 days | Yes - officeguest.example.com | orders@example.com |

## Notes

- **A supplier who orders by email, not WhatsApp** (like Frigo Atlântico's
  `orders@example.com` above) needs a real `systems.email.adapter` (`imap`
  or `gmail` - `config/hotel.yaml`) and their address in
  `procurement.supplier_emails` (`config/agent.yaml`) before
  `tools/review.py send` can actually deliver it - see
  `docs/integrations.md`. Left unconfigured, `send` refuses an
  `email`-channel order and keeps the approval rather than send it over
  WhatsApp instead.
- **Routine, low-risk suppliers** (short lead time, small typical order
  value, no history of price disputes) are candidates for
  `procurement.routine_orders.suppliers` in `config/agent.yaml` - the one
  path that can send with nobody reviewing it first, and only once you have
  deliberately turned that on (see `workflows/90-go-live.md`).
- If a supplier's minimum order value or a delivery window matters, write it
  here - the engine's `supplier-consolidate` rule only checks whether lines
  share a supplier, not a minimum order value (see `docs/how-it-works.md`
  design decision 5 and `specs/procurement-supply-ai.md` open question 4 in
  this family's source spec).
