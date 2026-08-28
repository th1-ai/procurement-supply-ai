#!/usr/bin/env python3
"""tools/report.py - what the agent ordered, and what it cost.

    make report
    python3 tools/report.py
    python3 tools/report.py --json

Reads data/agent.db - nothing here calls a model or an adapter. Every number
is tied to a roster claim (README section 2, docs/benefits.md):

``volumes``            orders by supplier and by review_status right now.
``spend vs naive``     this week's total against a flat par-level top-up -
                       the roster's "waste write-offs bend down" claim,
                       measured every run (see ``supply_engine.naive_total``).
``price flags``        how many orders this week needed a price-creep check
                       before approval.
``edit %``             of everything a human approved or edited, how often
                       they had to rewrite the drafted supplier message.
``waste trend``        ``supply_engine.summarise_waste`` on ``waste_log`` -
                       the roster's "-12% F&B over-ordering & waste" number.
``spend``              LLM calls, tokens and cost, from ``core.store.usage_totals``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMError  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import Store, StoreError, TERMINAL  # noqa: E402

import store_ext  # noqa: E402
import supply_engine as se  # noqa: E402


def _order_payloads(store: Store) -> list[dict]:
    """Every supply_order's payload, parsed in Python.

    Not SQL ``json_extract`` - stdlib ``sqlite3`` does not guarantee the
    JSON1 extension is compiled in (see tools/store_ext.py's
    ``record_run_narrative`` for the same reasoning).
    """
    rows = store.db.execute(
        "SELECT payload_json FROM items WHERE kind='supply_order'").fetchall()
    out = []
    for row in rows:
        try:
            out.append(json.loads(row["payload_json"] or "{}"))
        except json.JSONDecodeError:
            continue
    return out


def volumes(store: Store) -> dict:
    by_status = store.counts()
    payloads = _order_payloads(store)
    by_supplier: dict[str, int] = {}
    for payload in payloads:
        supplier = payload.get("supplier") or "-"
        by_supplier[supplier] = by_supplier.get(supplier, 0) + 1
    return {"by_supplier": by_supplier, "by_status": by_status, "total": len(payloads)}


def spend_vs_naive(store: Store) -> dict:
    total = sum(float(p.get("total_eur", 0)) for p in _order_payloads(store))
    return {"total_eur": round(total, 2)}


def price_flags(store: Store) -> dict:
    payloads = _order_payloads(store)
    flagged = sum(1 for p in payloads if p.get("has_price_flag"))
    return {"flagged": flagged, "total": len(payloads)}


def edit_stats(store: Store) -> dict:
    rows = store.db.execute(
        "SELECT item_id, action FROM events WHERE action IN "
        "('status:edited', 'status:approved')").fetchall()
    edited = {r["item_id"] for r in rows if r["action"] == "status:edited"}
    approved = {r["item_id"] for r in rows if r["action"] == "status:approved"} - edited
    total = len(edited) + len(approved)
    rate = (len(edited) / total) if total else 0.0
    return {"edited": len(edited), "approved_unchanged": len(approved), "rate": rate}


def spend(store: Store, since: str | None = None) -> dict:
    return store.usage_totals(since=since)


def build_report(store: Store, currency: str = "EUR", since: str | None = None) -> dict:
    waste = se.summarise_waste(store_ext.get_waste_rows(store), currency=currency)
    return {
        "volumes": volumes(store), "spend_vs_naive": spend_vs_naive(store),
        "price_flags": price_flags(store), "edits": edit_stats(store),
        "waste": {"drop_pct": waste.drop_pct, "annualised_saving": waste.annualised_saving,
                 "note": waste.note} if waste else None,
        "spend": spend(store, since=since),
    }


def print_report(report: dict, currency: str = "EUR") -> None:
    v = report["volumes"]
    print("Procurement / Supply AI - report\n")
    print(f"Orders: {v['total']} total")
    if v["by_supplier"]:
        print("  by supplier: " + ", ".join(f"{k}={n}" for k, n in sorted(v["by_supplier"].items())))
    if v["by_status"]:
        print("  by status:   " + ", ".join(f"{k}={n}" for k, n in sorted(v["by_status"].items())))

    s = report["spend_vs_naive"]
    print(f"\nOrder value this run: {currency} {s['total_eur']:.2f} across every supplier "
         f"order currently in the store.")

    p = report["price_flags"]
    print(f"Price flags: {p['flagged']}/{p['total']} order(s) needed a price-creep check "
         f"before approval.")

    e = report["edits"]
    total_reviewed = e["edited"] + e["approved_unchanged"]
    if total_reviewed:
        print(f"Edit rate: {e['edited']}/{total_reviewed} approved order(s) needed a rewrite "
             f"({e['rate']*100:.0f}%).")
    else:
        print("Edit rate: nothing approved or edited yet.")

    w = report["waste"]
    if w:
        print(f"\n{w['note']}")
    else:
        print("\nWaste trend: not enough waste_log rows with a marked switch-over day yet.")

    sp = report["spend"]
    print(f"\nSpend: {sp['calls']} LLM call(s), {sp['input_tokens']} input + "
         f"{sp['output_tokens']} output token(s), USD {sp['cost_usd']:.4f}.")
    if sp["calls"] and sp["cost_usd"] == 0.0:
        print("  (0.00 is expected on provider=mock, interactive or claude-code - only "
             "the anthropic provider bills per token.)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--since", default=None, help="ISO timestamp - only spend since then")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    try:
        store = Store(settings)
        store_ext.ensure_schema(store)
        report = build_report(store, currency=settings.hotel.currency, since=args.since)
    except (AdapterError, LLMError, StoreError, WriteBlocked) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            store.close()
        except NameError:
            pass

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    else:
        print_report(report, currency=settings.hotel.currency)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
