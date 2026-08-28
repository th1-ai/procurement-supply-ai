"""tools/supplier_ordering.py - Supplier Ordering AI ("The Buyer"), folded into

the parent's own loop and tables - see specs/supplier-ordering-ai.md and
docs/sub-agents.md. Off by default (``subagents.supplier_ordering.enabled``);
the parent is fully useful without it.

What it adds to an order the parent already built, once enabled:

- a supplier with an online ordering system (``config/agent.yaml``'s
  ``subagents.supplier_ordering.portal_suppliers``) gets a ``channel: portal``
  label and an audit line once a human approves and sends it - see
  ``tools/review.py``'s ``send`` command.
- a supplier with no portal gets an LLM-drafted, ready-to-send message
  (``order-message``) for the human to read before sending - this module's
  :func:`draft_order_message`.

Nothing here bypasses human approval on its own. Only the parent's routine-
order lane (``tools/run.py``'s ``attempt_routine_send``) can do that, and
only once a hotel has opted in twice over - see docs/how-it-works.md design
decision 6.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.config import Settings
from core.llm import LLMResult, complete
from core.store import Item, Store
from core.templates import build_prompt

import supply_engine as se

SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "prompts" / "schemas"


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS_DIR / f"{name}.json").read_text(encoding="utf-8"))


ORDER_MESSAGE_SCHEMA = _schema("order-message")


def enabled(settings: Settings) -> bool:
    return bool(settings.agent_get("subagents.supplier_ordering.enabled", False))


def portal_suppliers(settings: Settings) -> list[str]:
    return list(settings.agent_get("subagents.supplier_ordering.portal_suppliers", []) or [])


def supplier_email(settings: Settings, supplier: str) -> str:
    """The address on file for ``supplier`` (``procurement.supplier_emails``,
    ``config/agent.yaml``), or ``""`` if none is set. Used by
    ``tools/review.py send`` when a drafted message's ``channel`` is
    ``email`` - see docs/integrations.md.
    """
    emails = settings.agent_get("procurement.supplier_emails", {}) or {}
    return str(emails.get(supplier, "") or "")


def needs_order_message(order: se.SupplierOrder, settings: Settings) -> bool:
    """A no-portal order, with the sub-agent on, needs a drafted message."""
    if not enabled(settings):
        return False
    return order.supplier not in portal_suppliers(settings)


def draft_order_message(settings: Settings, store: Store, item: Item, order: se.SupplierOrder,
                        *, fixture_id: str, provider: str | None = None) -> dict:
    """The one LLM call this sub-agent makes. Raises on a schema error like any task."""
    payload = {"supplier": order.supplier,
              "lines": [{"name": l.name, "qty": l.qty, "unit": l.unit} for l in order.lines],
              "total_eur": order.total_eur}
    effort = settings.agent_get("llm.order_message_effort", "low")
    prompt = build_prompt("order-message", settings=settings, item=payload, fixture_id=fixture_id)
    result: LLMResult = complete("order-message", prompt, ORDER_MESSAGE_SCHEMA, settings=settings,
                                 provider=provider, store=store, item_id=item.id,
                                 fixture_id=fixture_id, effort=effort)
    return result.data or {}


def channel_for(item: Item, settings: Settings) -> str:
    """What ``tools/review.py send`` should label this order as, once approved.

    ``item.payload`` carries ``supplier`` and ``has_portal`` as they stood
    when the order was generated; ``item.draft`` carries the LLM-drafted
    message (with its own ``channel``) when one was written.
    """
    payload = item.payload or {}
    if enabled(settings) and payload.get("has_portal"):
        return "portal"
    draft = item.draft or {}
    if draft.get("channel"):
        return str(draft["channel"])
    return "manual"
