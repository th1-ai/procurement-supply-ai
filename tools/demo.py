#!/usr/bin/env python3
"""tools/demo.py - one full weekly cycle on the bundled fixtures, zero credentials.

    make demo
    python3 tools/demo.py

Forces `llm.provider=mock` and `mode=shadow` regardless of config/hotel.yaml,
so this always works on a fresh clone with a blank .env. It runs against its
own database (data/demo/demo.db) and a fixed `--as-of` date that matches
fixtures/hotel/{reservations,covers,supply_items}.json, so running it twice
always shows the same forecast, the same flagged price and the same waste
trend. It never touches data/agent.db (that is `make run`'s file).

Prints one line every check reads for the pass/fail signal:

    DEMO OK — 4 items processed, 4 drafted, 0 sent (shadow)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings, sub_data_dir  # noqa: E402
from core.llm import LLMError  # noqa: E402
from core.log import summary_line  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import Store, StoreError  # noqa: E402

import store_ext  # noqa: E402
import supply_engine as se  # noqa: E402
from run import one_pass  # noqa: E402

AS_OF = "2026-09-01"  # matches fixtures/hotel/{reservations,covers,supply_items}.json


def main() -> int:
    try:
        # demo=True forces mock provider, shadow mode and the mock adapter for every
        # system, whatever config/hotel.yaml says - a demo can never read a real
        # mailbox or PMS, or send anything for real. See docs/how-it-works.md.
        settings = load_settings(demo=True)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()  # every `make demo` is a clean, repeatable run

    try:
        store = Store(settings, path=demo_db)
        store_ext.ensure_schema(store)
    except (AdapterError, StoreError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Procurement / Supply AI demo - forecasting from {AS_OF} on fixtures/hotel/\n")

    try:
        code, stats = one_pass(settings, store, as_of=AS_OF, limit=20, provider="mock")
    except (AdapterError, LLMError, StoreError, WriteBlocked) as exc:
        print(f"error: {exc}", file=sys.stderr)
        store.close()
        return 1
    if code != 0:
        print("demo run did not complete cleanly - this should never happen on the mock "
             "provider", file=sys.stderr)
        store.close()
        return 1

    print("\nCurrent orders:")
    for item in store.list_items(kind="supply_order", limit=20):
        payload = item.payload or {}
        flag = "  [PRICE FLAG]" if payload.get("has_price_flag") else ""
        print(f"  {payload.get('supplier', '-'):<24} {settings.hotel.currency} "
             f"{payload.get('total_eur', 0):>8.2f}  status={item.review_status}{flag}")

    ws = se.summarise_waste(store_ext.get_waste_rows(store), currency=settings.hotel.currency)
    if ws:
        print(f"\n{ws.note}")

    print(f"\n{stats['needs_human']} of {stats['processed']} order(s) need a person to look "
         f"first (a flagged price always does - see docs/safety.md).")
    print("Nothing was sent: mode is shadow, and demo never calls send() at all.")
    print("Next: `make review` to see the drafts, or read workflows/10-procurement.md.\n")

    print(f"DEMO OK — {summary_line(stats, settings.mode)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
