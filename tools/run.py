#!/usr/bin/env python3
"""tools/run.py - The Quartermaster's weekly loop: forecast, decide, draft, queue.

    python3 tools/run.py --once
    python3 tools/run.py --watch
    python3 tools/run.py --once --dry-run
    python3 tools/run.py --once --provider mock
    python3 tools/run.py --once --as-of 2026-09-01
    python3 tools/run.py --once --limit 5

One pass: fetch occupancy + covers over the horizon -> supply_engine builds
one draft purchase order per supplier, with the reasoning on every line ->
each order gets a `price-flag` LLM note if it tripped the 90-day price
check, and (Supplier Ordering sub-agent only) an `order-message` LLM draft
if its supplier has no online portal -> queued for review, or - for a
routine, low-risk, un-flagged supplier with autonomy switched on - an
attempted automatic send that falls back to review if the write is blocked
-> one run-level `purchase-note` narrates the whole pass. See
docs/how-it-works.md for the full picture, including the mermaid diagram.

Exit codes: 0 ok, 3 waiting on an `interactive` answer (see the message),
1 a real error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import AdapterError, get_messaging, get_pms  # noqa: E402
from core.config import ConfigError, Settings, load_settings  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive, LLMSchemaError, complete  # noqa: E402
from core.log import Run, get_logger, summary_line  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import Item, Store, StoreError  # noqa: E402
from core.templates import build_prompt  # noqa: E402

import store_ext  # noqa: E402
import supplier_ordering  # noqa: E402
import supply_engine as se  # noqa: E402

log = get_logger("run")
FIXTURES_HOTEL = REPO_ROOT / "fixtures" / "hotel"
SCHEMAS_DIR = REPO_ROOT / "prompts" / "schemas"
PRICE_FLAG_SCHEMA = json.loads((SCHEMAS_DIR / "price-flag.json").read_text(encoding="utf-8"))
PURCHASE_NOTE_SCHEMA = json.loads((SCHEMAS_DIR / "purchase-note.json").read_text(encoding="utf-8"))


def _rules(settings: Settings) -> dict:
    configured = settings.agent_get("procurement.rules", {}) or {}
    return {**se.DEFAULT_RULES, **configured}


def _params(settings: Settings) -> dict:
    return {
        "covers_per_occ_room": float(settings.agent_get(
            "procurement.covers_per_occupied_room", se.DEFAULT_COVERS_PER_OCC_ROOM)),
        "par_buffer_pct": float(settings.agent_get(
            "procurement.par_buffer_pct", se.DEFAULT_PAR_BUFFER_PCT)),
        "waste_cap_pct": float(settings.agent_get(
            "procurement.waste_cap_pct", se.DEFAULT_WASTE_CAP_PCT)),
        "price_watch_threshold_pct": float(settings.agent_get(
            "procurement.price_watch_threshold_pct", se.DEFAULT_PRICE_THRESHOLD_PCT)),
    }


def _fixtures_dir(settings: Settings) -> Path:
    configured = settings.systems.pms.get("fixtures_dir")
    return Path(configured) if configured else FIXTURES_HOTEL


def fetch_demand_inputs(settings: Settings, as_of: str, horizon_days: int
                        ) -> tuple[list[dict], list[dict]]:
    """PMS occupancy has an adapter; restaurant covers do not (design decision 1)."""
    pms = get_pms(settings)
    window_end = (date.fromisoformat(as_of) + timedelta(days=horizon_days)).isoformat()
    reservations = [
        {"check_in": r.check_in, "check_out": r.check_out, "status": r.status}
        for r in pms.list_reservations(as_of, window_end)]
    covers = se.load_covers(_fixtures_dir(settings))
    return reservations, covers


def process_order(settings: Settings, store: Store, order: se.SupplierOrder, *,
                  week_start: str, provider: str | None = None) -> tuple[Item, bool]:
    """Turn one draft supplier order into a fully-processed ``items`` row.

    Idempotent at two levels (see docs/how-it-works.md "Idempotency"):

    Row-level: ``external_id`` is ``<week_start>:<supplier-slug>``, so
    regenerating the same week is a no-op once the item has left ``new``.
    We check that *before* touching the payload - ``store.upsert_item``
    would otherwise happily refresh the payload of an already-``sent``
    order the moment ``on_hand`` moves (e.g. after a delivery), silently
    rewriting the historical record of what was actually ordered.

    Stage-level: an order can need up to two independent LLM calls
    (``price-flag`` only if a line tripped the threshold; ``order-message``
    only if the Supplier Ordering sub-agent is on and the supplier has no
    portal). With ``llm.provider: interactive`` the first can succeed on one
    pass and the second can pend on the very next line - so completeness is
    checked per stage, on the item's own stored fields, never just "does
    this item exist".
    """
    external_id = f"{week_start}:{se.slugify(order.supplier)}"
    existing = store.get_by_external("procurement", external_id)
    if existing is not None and existing.review_status != "new":
        return existing, False

    needs_flag = order.has_price_flag
    needs_message = supplier_ordering.needs_order_message(order, settings)
    payload = {"week_start": week_start, "supplier": order.supplier,
              "lines": [l.as_dict() for l in order.lines], "total_eur": order.total_eur,
              "reason_summary": order.reason_summary, "has_price_flag": needs_flag,
              "has_portal": order.has_portal}
    if existing is None:
        item = store.upsert_item("procurement", external_id, kind="supply_order", payload=payload)
    else:
        # Still 'new': a retry after a pending interactive answer. Do NOT
        # pass `payload` through upsert_item here - it unconditionally
        # overwrites payload_json when the dict differs, which would wipe
        # out `price_flag_note` recorded by an earlier stage in THIS same
        # multi-stage pass. Keep the existing row (and its partial
        # progress) exactly as it is; only the stage blocks below add to it.
        item = existing
    fixture_id = external_id.replace(":", "__")

    if needs_flag and not (item.payload or {}).get("price_flag_note"):
        flagged = [l.as_dict() for l in order.lines if l.price_flagged]
        prompt = build_prompt("price-flag", settings=settings,
                              item={"supplier": order.supplier, "flagged_lines": flagged},
                              fixture_id=fixture_id)
        try:
            result = complete("price-flag", prompt, PRICE_FLAG_SCHEMA, settings=settings,
                              provider=provider, store=store, item_id=item.id,
                              fixture_id=fixture_id,
                              effort=settings.agent_get("llm.price_flag_effort", "low"))
        except LLMSchemaError as exc:
            store.set_fields(item.id, error=str(exc))
            return store.transition(item.id, "needs_human", "agent",
                                    {"error": "price_flag_schema_error"}), True
        note = (result.data or {}).get("flag_note", "")
        item = store.set_fields(item.id, payload={**item.payload, "price_flag_note": note})

    if needs_message and item.draft is None:
        try:
            data = supplier_ordering.draft_order_message(settings, store, item, order,
                                                          fixture_id=fixture_id, provider=provider)
        except LLMSchemaError as exc:
            store.set_fields(item.id, error=str(exc))
            return store.transition(item.id, "needs_human", "agent",
                                    {"error": "order_message_schema_error"}), True
        item = store.set_fields(item.id, draft=data)

    routine_suppliers = settings.agent_get("procurement.routine_orders.suppliers", []) or []
    max_routine_eur = float(settings.agent_get("procurement.routine_orders.max_total_eur", 0))
    autonomy = settings.agent_get("procurement.routine_orders.autonomy", "draft")
    if (not needs_flag and autonomy == "send" and settings.is_live
            and se.is_routine(order, routine_suppliers, max_routine_eur)):
        return attempt_routine_send(settings, store, item, order), True

    status = "needs_human" if needs_flag else "pending_review"
    updated = store.transition(item.id, status, "agent",
                               {"supplier": order.supplier, "total_eur": order.total_eur})
    return updated, True


def attempt_routine_send(settings: Settings, store: Store, item: Item,
                         order: se.SupplierOrder) -> Item:
    """The one path that can send with nobody reviewing it first.

    Attempted, not guaranteed: ``send_message`` is gated by default (see
    docs/how-it-works.md design decision 6), so out of the box this always
    falls back to ``pending_review`` - the same pattern
    ``revenue-management-ai`` uses for its own auto-publish path.
    """
    store.transition(item.id, "dispatched", "agent", {"routine": True})
    chat_ids = settings.agent_get("procurement.supplier_chat_ids", {}) or {}
    chat_id = chat_ids.get(order.supplier) or f"supplier:{se.slugify(order.supplier)}"
    text = se.routine_order_message(order, settings.hotel.name)
    messaging = get_messaging(settings)
    try:
        result = messaging.send(chat_id, text, item=item)
    except WriteBlocked as exc:
        log.info("routine send blocked, falling back to review", item_id=item.id,
                reason=str(exc))
        return store.transition(item.id, "pending_review", "agent", {"blocked": str(exc)})
    store.set_fields(item.id, sent_message_id=(result or {}).get("message_id"),
                     draft={"message": text, "channel": "whatsapp"})
    return store.transition(item.id, "auto_sent", "agent",
                            {"channel": "whatsapp", "routine": True})


def purchase_note(settings: Settings, store: Store, run_id: str, week_start: str,
                  result: se.ProposalResult, provider: str | None = None) -> str | None:
    """One narrative per run - cosmetic, never gates a decision. Degrades to None.

    ``llm.provider: mock`` (``make demo``, ``make test``) never calls a
    model for this: it computes the note straight from ``result``
    (``supply_engine.narrate_run``), so the story always matches whatever
    catalogue and demand are actually loaded, never a canned fixture pinned
    to the original sample data (see docs/how-it-works.md "Deterministic
    decisioning, LLM for language").
    """
    effective_provider = provider or settings.llm.provider
    if effective_provider == "mock":
        note = se.narrate_run(result, settings.hotel.name, settings.hotel.currency)
        store_ext.record_run_narrative(store, run_id, note)
        return note

    summary = {"week_start": week_start, "orders": [o.as_dict() for o in result.orders][:8],
              "total_eur": result.total_eur, "naive_total_eur": result.naive_total_eur,
              "demand": {"room_nights": result.demand.room_nights,
                        "covers_total": result.demand.covers_total,
                        "pct_capacity": result.demand.pct_capacity},
              "decision_line": result.decision_line}
    prompt = build_prompt("purchase-note", settings=settings, item=summary,
                          fixture_id=f"weekly-{week_start}")
    try:
        out = complete("purchase-note", prompt, PURCHASE_NOTE_SCHEMA, settings=settings,
                       provider=provider, store=store, item_id=None,
                       fixture_id=f"weekly-{week_start}",
                       effort=settings.agent_get("llm.purchase_note_effort", "low"))
    except LLMSchemaError as exc:
        log.warn("purchase-note schema error, skipping narrative", error=str(exc))
        return None
    note = (out.data or {}).get("note")
    store_ext.record_run_narrative(store, run_id, note)
    return note


def one_pass(settings: Settings, store: Store, *, as_of: str, limit: int,
            provider: str | None) -> tuple[int, dict]:
    """One weekly pass. Under ``--dry-run`` this never touches the store at
    all - not a seed row, not a ``runs`` row, not an item, not a model call -
    see docs/how-it-works.md "Idempotency" and workflows/99-troubleshooting.md.
    """
    stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0, "skipped": 0}
    horizon_days = int(settings.agent_get("procurement.horizon_days", se.DEFAULT_HORIZON_DAYS))
    catalog_path = FIXTURES_HOTEL / "supply_items.json"

    if settings.dry_run:
        catalog = (store_ext.get_catalog(store) if store_ext.is_seeded(store, "supply_items")
                  else store_ext.load_json_fixture(catalog_path))
        reservations, covers = fetch_demand_inputs(settings, as_of, horizon_days)
        result = se.run_order_proposal(catalog, reservations, covers, _rules(settings),
                                       _params(settings), as_of=as_of,
                                       capacity_rooms=settings.hotel.rooms,
                                       horizon_days=horizon_days, currency=settings.hotel.currency)
        for line in result.thinking_log:
            print(f"  [dry-run] {line}")
        print()
        for order in result.orders[:limit]:
            flag = "  [PRICE FLAG]" if order.has_price_flag else ""
            print(f"  [dry-run] would queue: {order.supplier:<24} {settings.hotel.currency} "
                 f"{order.total_eur:>8.2f}{flag}")
            stats["processed"] += 1
            stats["drafted"] += 1
            if order.has_price_flag:
                stats["needs_human"] += 1
        print("\n[dry-run] nothing written: no item, no seeded row, no run row, no model call.")
        return 0, stats

    with Run("procurement", settings, store) as run:
        store_ext.seed_catalog(store, catalog_path)
        store_ext.seed_waste_log(store, FIXTURES_HOTEL / "waste_log.json")
        reservations, covers = fetch_demand_inputs(settings, as_of, horizon_days)
        catalog = store_ext.get_catalog(store)
        result = se.run_order_proposal(catalog, reservations, covers, _rules(settings),
                                       _params(settings), as_of=as_of,
                                       capacity_rooms=settings.hotel.rooms,
                                       horizon_days=horizon_days, currency=settings.hotel.currency)
        for line in result.thinking_log:
            log.info("thinking", text=line)

        for order in result.orders[:limit]:
            try:
                item, did_work = process_order(settings, store, order, week_start=as_of,
                                               provider=provider)
            except LLMPendingInteractive as exc:
                run.stats = dict(stats)
                print(str(exc))
                return 3, stats
            if not did_work:
                stats["skipped"] += 1
                continue
            stats["processed"] += 1
            if item.review_status == "auto_sent":
                stats["sent"] += 1
            elif item.review_status == "needs_human":
                stats["needs_human"] += 1
                stats["drafted"] += 1
            elif item.review_status == "pending_review":
                stats["drafted"] += 1
            log.info("queued", item_id=item.id, supplier=order.supplier,
                     status=item.review_status, total_eur=order.total_eur)

        reaped = store.reap_stuck_sending()
        if reaped:
            log.warn("reaped stuck sends", count=len(reaped))

        # One purchase-note per week, not per pass (idempotency, see
        # docs/how-it-works.md): a week that already has a note and nothing
        # new to report this pass never re-parks an interactive prompt or
        # re-calls the model - it just re-shows what was already said.
        already_noted = store_ext.has_purchase_note(store, as_of)
        if not already_noted or stats["processed"] > 0:
            try:
                note = purchase_note(settings, store, run.id, as_of, result, provider=provider)
            except LLMPendingInteractive as exc:
                run.stats = dict(stats)
                print(str(exc))
                return 3, stats
            if note:
                store_ext.save_purchase_note(store, as_of, note, run.id)
                print(f"\n{note}\n")
        else:
            cached_note = store_ext.get_purchase_note(store, as_of)
            if cached_note:
                print(f"\n{cached_note}\n")
        run.stats = dict(stats)
    return 0, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="run a single pass (default)")
    mode_group.add_argument("--watch", action="store_true",
                            help="keep running on the configured interval")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write nothing, even in live mode")
    parser.add_argument("--limit", type=int, default=20, help="max supplier orders per pass")
    parser.add_argument("--provider", default=None, help="override llm.provider for this run")
    parser.add_argument("--as-of", default=None,
                        help="ISO date to forecast from (default: today). Fix this for a "
                             "reproducible run against fixtures.")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="override the --watch interval in seconds (default: 86400, i.e. "
                             "daily - for a real deployment use `make schedule` instead)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    as_of = args.as_of or date.today().isoformat()

    try:
        store = Store(settings)
        store_ext.ensure_schema(store)
    except (AdapterError, StoreError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    def run_once() -> tuple[int, dict]:
        return one_pass(settings, store, as_of=as_of, limit=args.limit, provider=args.provider)

    try:
        if args.watch:
            # --watch is a convenience for a laptop or a small always-on box.
            # A real deployment uses `make schedule` (cron/launchd/systemd) against
            # the `schedule:` block in config/agent.yaml instead - see README section 9.
            poll_seconds = args.poll_seconds or 86400
            while True:
                code, stats = run_once()
                print(summary_line(stats, settings.mode))
                if code != 0:
                    return code
                time.sleep(poll_seconds)
        code, stats = run_once()
        print(summary_line(stats, settings.mode))
        return code
    except (AdapterError, LLMError, StoreError, WriteBlocked) as exc:
        hint = f"\n  -> {exc.hint}" if getattr(exc, "hint", "") else ""
        print(f"error: {exc}{hint}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
