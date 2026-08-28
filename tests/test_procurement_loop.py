"""The full loop with provider=mock - what `make demo` and `make test` both
exercise. No network, no credentials - see docs/how-it-works.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.config import (AdapterConfig, ContactsConfig, HotelConfig, LLMConfig,  # noqa: E402
                         PrivacyConfig, ReviewConfig, Settings, SystemsConfig)
from core.store import Store  # noqa: E402

import run  # noqa: E402
import store_ext  # noqa: E402

AS_OF = "2026-09-01"  # matches fixtures/hotel/{reservations,covers,supply_items}.json


def _settings(**agent_overrides) -> Settings:
    agent = {"subagents": {"supplier_ordering": {"enabled": False, "portal_suppliers": []}},
            "procurement": {"routine_orders": {"autonomy": "draft", "suppliers": [],
                                               "max_total_eur": 0}}}
    agent.update(agent_overrides)
    return Settings(
        hotel=HotelConfig(name="Hotel Aurora", rooms=42, currency="EUR", languages=["en"]),
        contacts=ContactsConfig(),
        systems=SystemsConfig(pms=AdapterConfig("mock"), email=AdapterConfig("mock"),
                              messaging=AdapterConfig("mock"), sheets=AdapterConfig("csv")),
        mode="shadow", llm=LLMConfig(provider="mock"), review=ReviewConfig(),
        privacy=PrivacyConfig(), agent=agent, root=REPO_ROOT)


def test_one_pass_queues_every_supplier_order_on_mock_provider(tmp_path):
    settings = _settings()
    store = Store(settings, path=tmp_path / "loop.db")
    store_ext.ensure_schema(store)

    code, stats = run.one_pass(settings, store, as_of=AS_OF, limit=20, provider="mock")

    assert code == 0
    assert stats["processed"] == 4  # Frigo Atlantico, Lisbon Bakery, Linens & Co, Office & Guest
    assert stats["needs_human"] == 1  # only the price-flagged Frigo Atlantico order
    assert stats["sent"] == 0  # shadow mode, no auto-send lane eligible

    items = store.list_items(kind="supply_order", limit=20)
    assert len(items) == 4
    statuses = {i.review_status for i in items}
    assert statuses == {"pending_review", "needs_human"}
    store.close()


def test_nothing_is_ever_sent_in_shadow_mode_even_when_approved(tmp_path):
    from core.review import WriteBlocked, assert_write_allowed

    settings = _settings()
    store = Store(settings, path=tmp_path / "shadow.db")
    store_ext.ensure_schema(store)
    run.one_pass(settings, store, as_of=AS_OF, limit=20, provider="mock")

    item = store.list_items(kind="supply_order", limit=1)[0]
    assert item.review_status in ("pending_review", "needs_human")  # one_pass already queued it
    approved = store.transition(item.id, "approved", "human")

    # core.review.evaluate_write: shadow blocks EVERY write, approved or not.
    try:
        assert_write_allowed(settings, "sheets_write", approved)
        raised = False
    except WriteBlocked:
        raised = True
    assert raised
    store.close()


def test_rerunning_the_same_week_is_a_clean_no_op(tmp_path):
    settings = _settings()
    store = Store(settings, path=tmp_path / "dedup.db")
    store_ext.ensure_schema(store)

    code1, stats1 = run.one_pass(settings, store, as_of=AS_OF, limit=20, provider="mock")
    code2, stats2 = run.one_pass(settings, store, as_of=AS_OF, limit=20, provider="mock")

    assert code1 == code2 == 0
    assert stats1["processed"] == 4
    assert stats2["processed"] == 0  # every order already left 'new' - nothing to redo
    assert stats2["skipped"] == 4
    assert len(store.list_items(kind="supply_order", limit=50)) == 4  # no duplicates
    store.close()


def test_dry_run_creates_no_rows_even_run_twice_on_a_fresh_store(tmp_path):
    settings = _settings()
    settings.dry_run = True
    store = Store(settings, path=tmp_path / "dry.db")
    store_ext.ensure_schema(store)

    code1, stats1 = run.one_pass(settings, store, as_of=AS_OF, limit=20, provider="mock")
    code2, stats2 = run.one_pass(settings, store, as_of=AS_OF, limit=20, provider="mock")

    assert code1 == code2 == 0
    assert stats1["processed"] == stats2["processed"] == 4  # computed and shown...
    assert store.list_items(kind="supply_order", limit=50) == []  # ...but never written
    assert store.counts() == {}
    row = store.db.execute("SELECT COUNT(*) AS n FROM supply_items").fetchone()
    assert row["n"] == 0  # not even the catalog was seeded
    assert store.db.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"] == 0
    store.close()
