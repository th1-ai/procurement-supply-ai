#!/usr/bin/env python3
"""tools/doctor.py - is Procurement / Supply AI configured and reachable right now?

    make doctor
    python3 tools/doctor.py

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, llm provider, every adapter, the store, knowledge) plus
this agent's own: the catalog and waste log fixtures exist, the three
prompts + schemas are present, and the routine-order / sub-agent config is
internally consistent. Exits 0 when everything passed, 1 when a FAIL line
needs fixing. Never a traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import AdapterError  # noqa: E402
from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402
from core.llm import LLMError  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import StoreError  # noqa: E402


def check_catalog_fixtures() -> Check:
    missing = [p for p in ("fixtures/hotel/supply_items.json", "fixtures/hotel/covers.json",
                           "fixtures/hotel/waste_log.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("catalog fixtures", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("catalog fixtures", PASS, "supply_items.json + covers.json + waste_log.json present")


def check_prompts() -> Check:
    missing = [p for p in ("prompts/purchase-note.md", "prompts/price-flag.md",
                           "prompts/order-message.md", "prompts/schemas/purchase-note.json",
                           "prompts/schemas/price-flag.json", "prompts/schemas/order-message.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("prompts", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("prompts", PASS, "purchase-note.md + price-flag.md + order-message.md present")


def check_routine_orders(settings: Settings) -> Check:
    autonomy = settings.agent_get("procurement.routine_orders.autonomy", "draft")
    suppliers = settings.agent_get("procurement.routine_orders.suppliers", []) or []
    if autonomy == "send" and not suppliers:
        return Check("routine orders", FAIL,
                     "autonomy is 'send' but procurement.routine_orders.suppliers is empty",
                     "Name at least one supplier, or set autonomy back to 'draft'.")
    if autonomy == "send" and "send_message" in settings.review.require_approval_for:
        return Check("routine orders", WARN,
                     "autonomy is 'send' but 'send_message' is still in "
                     "review.require_approval_for - the automatic send will always fall "
                     "back to pending_review",
                     "This is the safe default. Remove send_message from "
                     "review.require_approval_for in config/hotel.yaml once you have "
                     "watched this lane's drafts for a while - see workflows/90-go-live.md.")
    return Check("routine orders", PASS, f"autonomy={autonomy}, {len(suppliers)} supplier(s)")


def check_supplier_ordering(settings: Settings) -> Check:
    on = bool(settings.agent_get("subagents.supplier_ordering.enabled", False))
    if not on:
        return Check("supplier ordering sub-agent", PASS, "off (parent works fully without it)")
    portals = settings.agent_get("subagents.supplier_ordering.portal_suppliers", []) or []
    return Check("supplier ordering sub-agent", PASS,
                f"on, {len(portals)} supplier(s) marked as having a portal")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="Procurement / Supply AI - doctor")

    try:
        checks = run_checks(settings, extra=[check_routine_orders, check_supplier_ordering])
        checks.append(check_catalog_fixtures())
        checks.append(check_prompts())
    except (AdapterError, LLMError, StoreError, WriteBlocked) as exc:
        checks = [Check("doctor", FAIL, str(exc), "")]
    return print_table(checks, title="Procurement / Supply AI - doctor")


if __name__ == "__main__":
    raise SystemExit(main())
