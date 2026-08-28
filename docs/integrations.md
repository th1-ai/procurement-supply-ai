# Connecting your systems

Every connector in this repo is one of three things, and the table says
which. We will not tell you an integration exists when it does not.

| Badge | Means |
|---|---|
| **built** | Written against the real API and tested against it. |
| **universal** | Works with any system through a common protocol: CSV, WhatsApp via your own UniPile account, a webhook. |
| **stub** | Interface only. Calling it raises a clear error with a recipe for adding it. |

Check what is actually working on your machine at any time:

```bash
make doctor
```

## What this agent needs

Procurement / Supply AI reads occupancy (PMS) and restaurant covers (no
adapter - see below), and, once you approve an order, writes an audit
record (Sheets) and, for a no-portal supplier's message, sends it - over
WhatsApp (Messaging) or, only when a real adapter is configured, email
(Email). It never reads email: `fetch_unread` / `fetch_thread` are never
called, and `systems.email.adapter` starts on the shared family default
(`mock`), which this agent treats as "not set up for this agent" and
refuses to send through - see the Email section below for exactly what
that refusal looks like.

### PMS - `systems.pms.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/hotel/reservations.json`. What `make demo` uses. |
| `csv` | universal | a CSV export | Reads `data/imports/reservations.csv`. **Start here.** Works with every PMS. |
| `cloudbeds` | built | OAuth app + refresh token | Live reads. This agent never writes to your PMS. |

**`csv` - the one that always works.** Export from your PMS and drop it in
`data/imports/reservations.csv`:

```
id, status, check_in, check_out, room_type_id, room_type_name, room_id,
adults, children, source, total, balance, currency, guest_email,
guest_first_name, guest_last_name, guest_phone, guest_country
```

Headers are matched loosely (`checkIn`, `check_in`, `Check In` all work).
Dates must be `YYYY-MM-DD`. Only `check_in`, `check_out` and `status` are
actually read by this agent - the rest can be blank.

**`cloudbeds`.** Create an app in the Cloudbeds developer portal, authorise
it once against your property, and put the result in `.env`:

```
CLOUDBEDS_CLIENT_ID=
CLOUDBEDS_CLIENT_SECRET=
CLOUDBEDS_REFRESH_TOKEN=
CLOUDBEDS_PROPERTY_ID=
```

Scope needed: `read:reservation`. This agent never requests a write scope.

### Restaurant covers - no adapter

No system in `core/adapters` models a restaurant reservation book, and
this family does not invent one just for this agent - see
`docs/how-it-works.md` design decision 1. `tools/supply_engine.py::load_covers`
reads `fixtures/hotel/covers.json` directly: a plain JSON list of
`{"day_offset": 0, "covers": 82}` rows for the horizon. In production, put
your own file at the same path (or point `systems.pms.fixtures_dir` at a
folder that also has one) - export it from your POS or table-booking
system however is easiest; there is no live sync, it is read fresh every
run.

### Email - `systems.email.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | The shared family default. This agent treats it as "email not set up" - see below. |
| `imap` | universal | an IMAP/SMTP mailbox | Any provider. Send-only for this agent - it never reads the mailbox. |
| `gmail` | built | a Gmail OAuth app | Send-only for this agent, same as `imap`. |

Only used for one thing: `tools/review.py send` delivering a Supplier
Ordering sub-agent's drafted message when that message's `channel` is
`email` (the model chooses `email` over `whatsapp` for a longer, itemised
order - `prompts/order-message.md`). Because this agent's own default is
"we do not touch email" (see above), `send` requires you to actually
choose a real adapter first - leaving `systems.email.adapter: mock` (the
shared default every repo in this family ships with) means `send` refuses
an `email`-channel order with a readable message and **keeps the
approval** rather than quietly deliver it over WhatsApp instead:

```
blocked <id> (approval kept): email adapter not configured - set systems.email
in config/hotel.yaml to a real adapter (imap or gmail), or use channel:
whatsapp - see docs/integrations.md.
```

Once you set `systems.email.adapter` to `imap` or `gmail`, also fill in
`procurement.supplier_emails` (`config/agent.yaml`) for every supplier who
takes orders by email - `send` refuses the same way, approval kept, if a
supplier has no address on file.

### Messaging - `systems.messaging.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | No credentials, always answers the same way. |
| `unipile` | built | your own UniPile account | WhatsApp on your own connected number. |
| `webhook` | universal | any URL | POST to Zapier, Make, n8n, or your own endpoint. |

Used for two things: the routine-order lane's automatic send (always
WhatsApp - `docs/how-it-works.md` design decision 6), and `tools/review.py
send` delivering a Supplier Ordering sub-agent's drafted message when its
`channel` is `whatsapp`. Set `procurement.supplier_chat_ids`
(`config/agent.yaml`) for every no-portal supplier before relying on
either. A `channel: email` order goes through Email above instead, never
through here - `send` always picks the adapter that matches the drafted
channel, and never silently substitutes one channel for another.

**`unipile`.** You create the account, you connect your number by QR code,
you own the credentials: `UNIPILE_DSN`, `UNIPILE_API_KEY`,
`UNIPILE_ACCOUNT_ID`. WhatsApp Business policy limits what you may send
outside a conversation the other side started; a supplier relationship
with regular ordering is normally fine, but read your provider's rules.

**`webhook`.** The simplest possible outbound: set `MESSAGING_WEBHOOK_URL`
and the agent POSTs `{chat_id, text, kind, hotel, sent_at}`. Your
automation tool delivers it however you like.

### Sheets - `systems.sheets.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `csv` | universal | nothing | Writes `data/exports/procurement_orders.csv`. |
| `google` | built | service account JSON | A live shared spreadsheet everyone on the team can see. |

Every order that actually sends - portal-labelled or not - gets one line
in `procurement_orders`: id, week, supplier, total, channel. This is the
practical order log a hotel checks against invoices.

For `google`: enable the Sheets API, create a service account and a JSON
key, save it as `service_account.json`, and share your spreadsheet with
the service account's email as an Editor. Set
`systems.sheets.spreadsheet_id` to the id from the sheet's URL.

### Everything else - Procurement (portal placement), and the family's usual stubs

`procurement`, `pos`, `accounting`, `reviews`, `calendar`, `payments` and
`locks` are **stubs**: the interface exists, nothing is implemented.
Calling one raises an error that tells you exactly this.

**Why `procurement` itself stays a stub.** Every supplier's online
ordering system is different - there is no generic "supplier portal" API
to build a universal adapter against, unlike a PMS or a mailbox. This repo
does not fake one. What it does instead, once the Supplier Ordering
sub-agent is on: label a portal supplier's order `channel: portal` and log
it to the Sheets audit trail on send, so you have a clear record of what
was meant to go to their portal - actually placing it there is still your
click, unless you implement that ONE supplier's specific API using the
recipe below.

## Implement your own

<a id="implement-your-own"></a>

The interface is small on purpose, and your Claude Code session can do
this with you in an afternoon. Open `claude` in this folder and paste:

> Read `docs/integrations.md#implement-your-own` and
> `core/adapters/base.py`. I need a Procurement adapter for
> **<your supplier's ordering system>**. Its API docs are at **<url>** and
> I have credentials in `.env` as `<VAR names>`. Copy
> `core/adapters/domain_stub.py`'s `Procurement` class as the shape,
> implement `ping`, `capabilities`, `list_suppliers` and `create_order`,
> register it in `core/adapters/__init__.py`, and stop before wiring it
> into `tools/supplier_ordering.py` so I can check `create_order` by hand
> first.

### The five steps

**1. Copy the closest existing adapter shape.** `core/adapters/pms_csv.py`
for a read-only feed, `messaging_webhook.py` for a simple POST-based
write. They are short and heavily commented.

**2. Implement `ping()` and `capabilities()` first.**

```python
def ping(self) -> HealthCheck:
    """Never raises. Returns ok=False with a fix_hint a hotel can act on."""

def capabilities(self) -> set[str]:
    """The method names that actually do something on this adapter."""
```

`make doctor` reads both. Getting them right first means the rest of the
work has a feedback loop.

**3. Implement the reads** (`list_suppliers`, or whatever the vendor's API
calls it). Map fields loosely - keep anything you do not have a place for
in a plain dict, do not drop it.

**4. Implement the write, with the guard.**

```python
from core.adapters.base import guarded_write

@guarded_write("procurement_order")
def create_order(self, order: dict) -> dict:
    ...
```

The decorator is not optional. Without it your adapter can write while the
agent is in shadow mode, which defeats the entire safety model. Add
`procurement_order` to `review.require_approval_for` in
`config/hotel.yaml` alongside the defaults once you wire this in.

**5. Register it.** One line in `core/adapters/__init__.py`:

```python
REGISTRY["procurement"]["yoursystem"] = "core.adapters.procurement_yoursystem:YourSystemProcurement"
```

Then wire the call into `tools/review.py::cmd_send` next to the existing
Sheets/Messaging calls, gated the same way, and run `make doctor`.

### Rules that matter

- **`ping()` never raises.** It returns `HealthCheck(ok=False, ...)` with a
  hint. A broken adapter must still produce a readable doctor table.
- **Every write is decorated.** No exceptions.
- **Rate limits belong in the adapter.** Use
  `core/adapters/_http.py:RateLimiter`. Retry 429 and 5xx with backoff;
  never retry a 4xx.
- **Never log a credential.** `core/log.py` masks anything whose key looks
  like a secret, but do not rely on it.
- **Write a test.** Copy `tests/test_core_adapters_mock_csv.py`. It should
  run with no network: feed your parser a fixture, check the dataclass
  that comes out.

### `core/` is shared

`core/` is identical in all 28 agents in this family. If you change
something in `core/`, keep it generic - a property-specific tweak belongs
in `tools/` or in your own adapter file, not in the shared runtime.
