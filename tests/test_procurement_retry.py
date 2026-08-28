"""The interactive-provider two-stage retry regression test.

An order can need up to two independent LLM calls: `price-flag` (only if a
line tripped the 90-day threshold) and `order-message` (only if the
Supplier Ordering sub-agent is on and the supplier has no portal). With
`llm.provider: interactive`, the first can succeed on one pass and the
second can pend on the very next line - see docs/how-it-works.md
"Idempotency" and tools/run.py::process_order's docstring.

This test proves three things a naive "does the item already have SOME
output" check would get wrong:

1. The first pending prompt is `price-flag`, not `order-message`.
2. After answering it, a retry does NOT re-ask `price-flag` - it resumes
   straight into `order-message` on the SAME item.
3. After answering that too, the item completes with both fields set,
   never having skipped a stage or duplicated a question.

Uses the real repo's data/pending/ (the mechanism is filesystem-based by
design - see core/llm.py) with a week_start far in the future so the
fixture ids can never collide with anything from `make demo` or a real
run, and cleans up every file it creates.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import pytest  # noqa: E402

from core.config import (AdapterConfig, ContactsConfig, HotelConfig, LLMConfig,  # noqa: E402
                         PrivacyConfig, ReviewConfig, Settings, SystemsConfig)
from core.llm import LLMPendingInteractive  # noqa: E402
from core.store import Store  # noqa: E402

import run  # noqa: E402
import store_ext  # noqa: E402
import supply_engine as se  # noqa: E402

WEEK_START = "2099-01-01"  # far future: fixture ids never collide with real runs
def _pending():
    """data/pending under the (sandboxed) repo root the code actually uses."""
    from core.config import sub_data_dir
    return sub_data_dir("pending")


def _settings() -> Settings:
    # Built directly, not via load_settings() - tests never read config/hotel.yaml
    # or config/agent.yaml (those are the hotel's own), per build-repo.md section 5.
    return Settings(
        hotel=HotelConfig(name="Hotel Aurora", rooms=42, currency="EUR", languages=["en"]),
        contacts=ContactsConfig(), systems=SystemsConfig(),
        mode="shadow", llm=LLMConfig(provider="interactive"), review=ReviewConfig(),
        privacy=PrivacyConfig(),
        agent={"subagents": {"supplier_ordering": {"enabled": True, "portal_suppliers": []}},
              "procurement": {"routine_orders": {"autonomy": "draft", "suppliers": [],
                                                 "max_total_eur": 0}}},
        root=REPO_ROOT)


def _order() -> se.SupplierOrder:
    line = se.LineForecast(
        item_id="sea-bass", name="Sea bass", category="fnb", supplier="Test Supplier",
        has_portal=False, unit="kg", on_hand=1.0, basis=5.0, target=5.5, capped=False,
        trimmed_units=0.0, qty=5.0, unit_cost=20.0, lead_days=1, perishable=True,
        price_flagged=True, price_flag_detail="Test Supplier's sea bass is 12% above baseline.",
        reason="test line - see supply_engine tests for real formula coverage")
    return se.SupplierOrder(supplier="Test Supplier", lines=[line],
                            reason_summary="Test Supplier: 5 kg Sea bass.")


def _cleanup() -> None:
    slug = se.slugify("Test Supplier")
    fixture_id = f"{WEEK_START}__{slug}"
    for task in ("price-flag", "order-message"):
        for suffix in (".prompt.md", ".schema.json", ".answer.json", ".answer.json.used"):
            path = _pending() / f"{task}-{fixture_id}{suffix}"
            path.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    _cleanup()
    yield
    _cleanup()


def test_interactive_resumes_at_pending_stage_without_reasking_or_skipping(tmp_path):
    settings = _settings()
    store = Store(settings, path=tmp_path / "retry.db")
    store_ext.ensure_schema(store)
    order = _order()
    slug = se.slugify("Test Supplier")

    # Pass 1: price-flag pends first (it is evaluated before order-message).
    with pytest.raises(LLMPendingInteractive) as exc1:
        run.process_order(settings, store, order, week_start=WEEK_START, provider="interactive")
    assert exc1.value.pending_id == f"price-flag-{WEEK_START}__{slug}"

    item = store.get_by_external("procurement", f"{WEEK_START}:{slug}")
    assert item is not None and item.review_status == "new"
    assert (item.payload or {}).get("price_flag_note") is None

    # Answer price-flag.
    answer_path = _pending() / f"price-flag-{WEEK_START}__{slug}.answer.json"
    answer_path.write_text(json.dumps({"flag_note": "12% above baseline - check before approving."}),
                           encoding="utf-8")

    # Pass 2: must NOT re-ask price-flag - must resume straight into order-message.
    with pytest.raises(LLMPendingInteractive) as exc2:
        run.process_order(settings, store, order, week_start=WEEK_START, provider="interactive")
    assert exc2.value.pending_id == f"order-message-{WEEK_START}__{slug}"

    item = store.get_by_external("procurement", f"{WEEK_START}:{slug}")
    assert item.review_status == "new"  # still not queued - one stage still pending
    assert (item.payload or {}).get("price_flag_note") == "12% above baseline - check before approving."
    assert item.draft is None  # order-message has not answered yet

    # A price-flag answer file must not have reappeared (would mean we re-asked it).
    assert not (_pending() / f"price-flag-{WEEK_START}__{slug}.prompt.md").exists()

    # Answer order-message.
    answer_path = _pending() / f"order-message-{WEEK_START}__{slug}.answer.json"
    answer_path.write_text(json.dumps({"message": "Please deliver 5kg sea bass.",
                                      "channel": "whatsapp"}), encoding="utf-8")

    # Pass 3: both stages done - the item completes and leaves 'new'.
    item, did_work = run.process_order(settings, store, order, week_start=WEEK_START,
                                       provider="interactive")
    assert did_work is True
    assert item.review_status == "needs_human"  # the price flag routes it to a human, always
    assert item.payload["price_flag_note"] == "12% above baseline - check before approving."
    assert item.draft == {"message": "Please deliver 5kg sea bass.", "channel": "whatsapp"}

    # Pass 4: idempotent - a further retry is a clean no-op, no new prompt files.
    item2, did_work2 = run.process_order(settings, store, order, week_start=WEEK_START,
                                         provider="interactive")
    assert did_work2 is False
    assert item2.id == item.id
    store.close()
