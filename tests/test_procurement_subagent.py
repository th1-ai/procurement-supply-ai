"""Supplier Ordering AI ("The Buyer") - see specs/supplier-ordering-ai.md and
docs/sub-agents.md. Off by default; the parent works fully without it.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.config import (ContactsConfig, HotelConfig, LLMConfig, PrivacyConfig,  # noqa: E402
                         ReviewConfig, Settings, SystemsConfig)
from core.store import Item  # noqa: E402

import supplier_ordering  # noqa: E402
import supply_engine as se  # noqa: E402


def _settings(enabled: bool, portal_suppliers: list[str]) -> Settings:
    return Settings(
        hotel=HotelConfig(name="Hotel Aurora", rooms=42), contacts=ContactsConfig(),
        systems=SystemsConfig(), mode="shadow", llm=LLMConfig(provider="mock"),
        review=ReviewConfig(), privacy=PrivacyConfig(),
        agent={"subagents": {"supplier_ordering": {"enabled": enabled,
                                                    "portal_suppliers": portal_suppliers}}},
        root=REPO_ROOT)


def _order(supplier: str) -> se.SupplierOrder:
    line = se.LineForecast(item_id="x", name="X", category="other", supplier=supplier,
                           has_portal=False, unit="pcs", on_hand=0, basis=1, target=1,
                           capped=False, trimmed_units=0, qty=1, unit_cost=1.0, lead_days=1,
                           perishable=False, price_flagged=False, price_flag_detail="",
                           reason="test")
    return se.SupplierOrder(supplier=supplier, lines=[line])


def test_off_by_default_needs_no_message_regardless_of_portal():
    settings = _settings(enabled=False, portal_suppliers=[])
    assert supplier_ordering.needs_order_message(_order("Frigo Atlântico"), settings) is False


def test_on_and_no_portal_needs_a_drafted_message():
    settings = _settings(enabled=True, portal_suppliers=["Linens & Co"])
    assert supplier_ordering.needs_order_message(_order("Frigo Atlântico"), settings) is True


def test_on_and_has_portal_needs_no_message():
    settings = _settings(enabled=True, portal_suppliers=["Linens & Co"])
    assert supplier_ordering.needs_order_message(_order("Linens & Co"), settings) is False


def test_channel_for_prefers_portal_label_when_enabled():
    settings = _settings(enabled=True, portal_suppliers=["Linens & Co"])
    item = Item(id="i1", kind="supply_order", source="procurement", external_id="e1",
               payload={"has_portal": True}, draft=None)
    assert supplier_ordering.channel_for(item, settings) == "portal"


def test_channel_for_falls_back_to_the_drafted_messages_channel():
    settings = _settings(enabled=True, portal_suppliers=[])
    item = Item(id="i2", kind="supply_order", source="procurement", external_id="e2",
               payload={"has_portal": False}, draft={"message": "hi", "channel": "whatsapp"})
    assert supplier_ordering.channel_for(item, settings) == "whatsapp"


def test_channel_for_is_manual_when_disabled():
    settings = _settings(enabled=False, portal_suppliers=["Linens & Co"])
    item = Item(id="i3", kind="supply_order", source="procurement", external_id="e3",
               payload={"has_portal": True}, draft=None)
    assert supplier_ordering.channel_for(item, settings) == "manual"


def test_draft_order_message_uses_the_mock_fixture():
    settings = _settings(enabled=True, portal_suppliers=[])
    from core.store import Store
    store = Store(settings, path=":memory:")
    item = Item(id="i4", kind="supply_order", source="procurement",
               external_id="2026-09-01:frigo-atlantico", payload={})
    data = supplier_ordering.draft_order_message(
        settings, store, item, _order("Frigo Atlântico"),
        fixture_id="2026-09-01__frigo-atlantico", provider="mock")
    assert data["channel"] == "whatsapp"
    assert "sea bass" in data["message"].lower()
    store.close()
