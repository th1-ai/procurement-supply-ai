#!/usr/bin/env python3
"""tools/review.py - work the review queue: list / show / approve / edit /
reject / retry / send / deliver.

    python3 tools/review.py list [--status pending_review] [--kind supply_order]
    python3 tools/review.py show <id>
    python3 tools/review.py approve <id> [--note "..."]
    python3 tools/review.py edit <id> --message-file draft.txt [--note "..."]
    python3 tools/review.py reject <id> --reason "price too high, calling around"
    python3 tools/review.py retry <id>          # re-queue a failed send
    python3 tools/review.py send                # send everything approved/edited
    python3 tools/review.py deliver <id>        # delivery has arrived: on_hand += qty

Only this tool writes `approved` / `edited` / `rejected` (core/review.py).
Only `send` writes `sending` / `sent`. There is deliberately no way to edit
a line's quantity here - every quantity already carries its own arithmetic
(see the item's `reason`); re-run with a different rule in config/agent.yaml
instead of hand-editing a number the agent already justified (spec section
4, "no edit-quantity control"). `edit` only replaces the drafted outbound
*message* text (only present when the Supplier Ordering sub-agent wrote
one - see docs/sub-agents.md).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import (AdapterError, AdapterNotConfigured, get_email,  # noqa: E402
                          get_messaging, get_sheets)
from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMError  # noqa: E402
from core.review import (WriteBlocked, approve, edit, list_queue, reject,  # noqa: E402
                         retry, show, stale_backlog)
from core.store import Store, StoreError, utcnow  # noqa: E402

import store_ext  # noqa: E402
import supplier_ordering  # noqa: E402


def _print_item_line(item, currency: str) -> None:
    payload = item.payload or {}
    flag = " [PRICE FLAG]" if payload.get("has_price_flag") else ""
    # `item.is_sample` is set by core (core/store.py) for anything read
    # through a mock adapter outside `make demo` - see docs/integrations.md
    # "Sample data is labelled".
    marker = "  [SAMPLE DATA]" if item.is_sample else ""
    print(f"  {item.id}  {item.review_status:<14} {payload.get('supplier', '-'):<24} "
         f"{currency} {payload.get('total_eur', 0):>8.2f}{flag}{marker}")


def cmd_list(store, settings, args) -> int:
    items = list_queue(store, status=args.status, kind=args.kind or "supply_order",
                       limit=args.limit)
    if not items:
        print("Nothing is waiting for you.")
        return 0
    print(f"{len(items)} order(s) waiting:\n")
    for item in items:
        _print_item_line(item, settings.hotel.currency)
    print("\nRun `python3 tools/review.py show <id>` for the full order.")
    return 0


def cmd_show(store, args) -> int:
    try:
        detail = show(store, args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if (detail["item"].get("payload") or {}).get("_sample"):
        print("[SAMPLE DATA] this item was read through a mock adapter, not your "
             "property - see docs/integrations.md.\n")
    print(json.dumps(detail, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_approve(store, args) -> int:
    item = approve(store, args.id, note=args.note or "")
    print(f"approved {item.id} - now in the send queue")
    return 0


def cmd_edit(store, args) -> int:
    item = store.get_item(args.id)
    if item is None:
        print(f"error: no item {args.id}", file=sys.stderr)
        return 1
    if item.draft is None or "message" not in (item.draft or {}):
        print("error: this order has no drafted message to edit (no portal-less supplier "
             "message was drafted - see docs/sub-agents.md). Reject and re-run instead.",
             file=sys.stderr)
        return 1
    message = Path(args.message_file).read_text(encoding="utf-8")
    new_draft = {**item.draft, "message": message}
    edit(store, args.id, new_draft, note=args.note or "")
    print(f"edited {item.id} - now in the send queue")
    return 0


def cmd_reject(store, args) -> int:
    item = reject(store, args.id, reason=args.reason or "")
    print(f"rejected {item.id}")
    return 0


def cmd_retry(store, args) -> int:
    item = retry(store, args.id)
    print(f"queued {item.id} for another send attempt")
    return 0


def cmd_deliver(store, args) -> int:
    item = store.get_item(args.id)
    if item is None:
        print(f"error: no item {args.id}", file=sys.stderr)
        return 1
    if item.review_status not in ("sent", "auto_sent"):
        print(f"error: {args.id} is '{item.review_status}', not sent yet - approve and send "
             f"it first.", file=sys.stderr)
        return 1
    if (item.payload or {}).get("delivered_at"):
        print(f"{args.id} was already marked delivered - nothing to do.")
        return 0
    store_ext.apply_delivery(store, item.payload or {})
    store.set_fields(item.id, payload={**item.payload, "delivered_at": utcnow()})
    print(f"delivered {item.id} - on_hand updated for {len(item.payload.get('lines', []))} "
         f"line(s). Run `python3 tools/report.py` to see current stock.")
    return 0


def cmd_stale(store, args) -> int:
    moved = stale_backlog(store)
    print(f"marked {len(moved)} order(s) stale. Nothing drafted or approved before go-live "
         f"will be sent - re-run tools/run.py once mode: live to get a fresh, current week.")
    return 0


def cmd_send(store, settings, args) -> int:
    if settings.mode == "shadow":
        print("mode is shadow: nothing leaves, even an approved order. Your approve/edit/"
             "reject decisions are recorded and teach the agent, but `send` will not "
             "actually send anything until mode: live in config/hotel.yaml - see "
             "workflows/90-go-live.md.")
        return 0
    claimed = store.claim_for_send(limit=args.limit)
    if not claimed:
        print("Nothing approved or edited is waiting to send.")
        return 0
    sheets = get_sheets(settings)
    messaging = get_messaging(settings)
    sent, blocked = 0, 0
    for item in claimed:
        payload = item.payload or {}
        draft = item.draft or {}
        channel = supplier_ordering.channel_for(item, settings)
        try:
            message_id = None
            if channel == "email":
                # Honesty check first: `channel: email` must actually reach a
                # mailbox, never quietly go out over WhatsApp instead (see
                # docs/integrations.md - only the adapter named here is real).
                if settings.systems.email.adapter == "mock":
                    raise AdapterNotConfigured(
                        "email adapter not configured - set systems.email in "
                        "config/hotel.yaml to a real adapter (imap or gmail), or use "
                        "channel: whatsapp - see docs/integrations.md.")
                to = supplier_ordering.supplier_email(settings, payload.get("supplier", ""))
                if not to:
                    raise AdapterNotConfigured(
                        f"no email address on file for '{payload.get('supplier', '')}' - add "
                        f"one to procurement.supplier_emails in config/agent.yaml.")
                email = get_email(settings)
                result = email.send(to, f"Order from {settings.hotel.name}",
                                    draft.get("message", ""), item=item)
                message_id = result.get("message_id") if isinstance(result, dict) else None
            elif channel == "whatsapp" and draft.get("message"):
                chat_ids = settings.agent_get("procurement.supplier_chat_ids", {}) or {}
                chat_id = (chat_ids.get(payload.get("supplier"))
                          or f"supplier:{payload.get('supplier', '')}")
                result = messaging.send(chat_id, draft["message"], item=item)
                message_id = result.get("message_id") if isinstance(result, dict) else None
            sheets.append("procurement_orders",
                          [[item.id, payload.get("week_start", ""), payload.get("supplier", ""),
                            payload.get("total_eur", 0), channel]], item=item)
        except (WriteBlocked, AdapterError) as exc:
            # Not a failure: the mode or a missing adapter blocked it, not
            # the order itself. The approval stands - retry once the cause
            # is fixed (workflows/90-go-live.md, docs/integrations.md).
            store.transition(item.id, "approved", "agent", {"blocked": str(exc)[:200]})
            print(f"blocked {item.id} (approval kept): {exc}")
            blocked += 1
            continue
        except Exception as exc:  # noqa: BLE001 - record and move on, never crash the batch
            store.mark_send_failed(item.id, str(exc))
            print(f"failed {item.id}: {exc}")
            blocked += 1
            continue
        store.mark_sent(item.id, message_id)
        print(f"sent {item.id} ({channel}) - logged to data/exports/procurement_orders.csv")
        sent += 1
    print(f"\n{sent} sent, {blocked} failed.")
    return 0 if blocked == 0 else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="what is waiting for a human")
    p_list.add_argument("--status", default=None)
    p_list.add_argument("--kind", default=None)
    p_list.add_argument("--limit", type=int, default=50)

    p_show = sub.add_parser("show", help="full detail for one order")
    p_show.add_argument("id")

    p_approve = sub.add_parser("approve", help="approve the order unchanged")
    p_approve.add_argument("id")
    p_approve.add_argument("--note", default="")

    p_edit = sub.add_parser("edit", help="rewrite the drafted supplier message, then queue it")
    p_edit.add_argument("id")
    p_edit.add_argument("--message-file", required=True)
    p_edit.add_argument("--note", default="")

    p_reject = sub.add_parser("reject", help="discard the order")
    p_reject.add_argument("id")
    p_reject.add_argument("--reason", default="")

    p_retry = sub.add_parser("retry", help="re-queue a failed send")
    p_retry.add_argument("id")

    p_send = sub.add_parser("send", help="send everything approved or edited")
    p_send.add_argument("--limit", type=int, default=20)

    p_deliver = sub.add_parser("deliver", help="delivery has arrived: on_hand += qty per line")
    p_deliver.add_argument("id")

    sub.add_parser("stale", help="go-live step: mark everything drafted or approved during "
                                 "shadow mode as stale - it was never sent and is out of date")

    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    try:
        store = Store(settings)
        store_ext.ensure_schema(store)
    except (AdapterError, StoreError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    try:
        if args.command == "list":
            return cmd_list(store, settings, args)
        if args.command == "show":
            return cmd_show(store, args)
        if args.command == "approve":
            return cmd_approve(store, args)
        if args.command == "edit":
            return cmd_edit(store, args)
        if args.command == "reject":
            return cmd_reject(store, args)
        if args.command == "retry":
            return cmd_retry(store, args)
        if args.command == "send":
            return cmd_send(store, settings, args)
        if args.command == "deliver":
            return cmd_deliver(store, args)
        if args.command == "stale":
            return cmd_stale(store, args)
        parser.error(f"unknown command {args.command}")
        return 2
    except (AdapterError, LLMError, StoreError, WriteBlocked) as exc:
        hint = f"\n  -> {exc.hint}" if getattr(exc, "hint", "") else ""
        print(f"error: {exc}{hint}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
