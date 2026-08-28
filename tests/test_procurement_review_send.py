"""tools/review.py's `send` command - channel honesty.

`channel: email` must actually route through the email adapter, never
quietly go out over WhatsApp instead, and must never claim to have sent
anything through an unconfigured mailbox. See docs/integrations.md and
SIMULATION.md finding 5.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from core.config import (AdapterConfig, ContactsConfig, HotelConfig, LLMConfig,  # noqa: E402
                         PrivacyConfig, ReviewConfig, Settings, SystemsConfig)
from core.store import Store  # noqa: E402

import review  # noqa: E402

WEEK_START = "2026-09-01"


def _settings(email_adapter: str = "mock", supplier_emails: dict | None = None,
             mode: str = "live") -> Settings:
    return Settings(
        hotel=HotelConfig(name="Hotel Aurora", rooms=42, currency="EUR", languages=["en"]),
        contacts=ContactsConfig(),
        systems=SystemsConfig(pms=AdapterConfig("mock"), email=AdapterConfig(email_adapter),
                              messaging=AdapterConfig("mock"), sheets=AdapterConfig("csv")),
        mode=mode, llm=LLMConfig(provider="mock"), review=ReviewConfig(),
        privacy=PrivacyConfig(),
        agent={"subagents": {"supplier_ordering": {"enabled": True, "portal_suppliers": []}},
              "procurement": {"routine_orders": {"autonomy": "draft", "suppliers": [],
                                                 "max_total_eur": 0},
                             "supplier_chat_ids": {}, "supplier_emails": supplier_emails or {}}},
        root=REPO_ROOT)


def _approved_item(store: Store, channel: str, supplier: str = "Frigo Atlântico"):
    external_id = f"{WEEK_START}:{supplier}"
    item = store.upsert_item(
        "procurement", external_id, kind="supply_order",
        payload={"week_start": WEEK_START, "supplier": supplier, "total_eur": 42.0,
                "has_price_flag": False})
    item = store.transition(item.id, "pending_review", "agent")
    store.set_fields(item.id, draft={"message": "Please deliver 2 kg sea bass.",
                                    "channel": channel})
    return store.transition(item.id, "approved", "human")


def _args(limit: int = 20) -> argparse.Namespace:
    return argparse.Namespace(limit=limit)


def test_email_channel_refused_when_adapter_is_still_the_shared_default(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_REPO_ROOT", str(tmp_path))
    settings = _settings(email_adapter="mock", supplier_emails={"Frigo Atlântico": "orders@example.com"})
    store = Store(settings, path=tmp_path / "send.db")
    item = _approved_item(store, channel="email")

    code = review.cmd_send(store, settings, _args())

    assert code == 1  # nothing actually sent
    reloaded = store.get_item(item.id)
    assert reloaded.review_status == "approved"  # approval kept, not failed
    assert reloaded.sent_at is None
    store.close()


def test_email_channel_refused_with_no_address_on_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_REPO_ROOT", str(tmp_path))
    settings = _settings(email_adapter="imap", supplier_emails={})  # real adapter, no address
    store = Store(settings, path=tmp_path / "send.db")
    item = _approved_item(store, channel="email")

    code = review.cmd_send(store, settings, _args())

    assert code == 1
    reloaded = store.get_item(item.id)
    assert reloaded.review_status == "approved"
    store.close()


def test_email_channel_sends_once_a_real_adapter_and_address_are_configured(
        tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_REPO_ROOT", str(tmp_path))
    settings = _settings(email_adapter="imap",
                        supplier_emails={"Frigo Atlântico": "orders@example.com"})
    store = Store(settings, path=tmp_path / "send.db")
    item = _approved_item(store, channel="email")

    sent = {}

    class _FakeEmail:
        def send(self, to, subject, body_md, **kwargs):
            sent["to"] = to
            sent["subject"] = subject
            return {"message_id": "fake-msg-1"}

    monkeypatch.setattr(review, "get_email", lambda settings: _FakeEmail())

    code = review.cmd_send(store, settings, _args())

    assert code == 0
    assert sent["to"] == "orders@example.com"
    reloaded = store.get_item(item.id)
    assert reloaded.review_status == "sent"
    assert reloaded.sent_message_id == "fake-msg-1"
    store.close()


def test_whatsapp_channel_is_unaffected_by_the_email_routing_change(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_REPO_ROOT", str(tmp_path))
    settings = _settings(email_adapter="mock")
    store = Store(settings, path=tmp_path / "send.db")
    item = _approved_item(store, channel="whatsapp")

    code = review.cmd_send(store, settings, _args())

    assert code == 0
    reloaded = store.get_item(item.id)
    assert reloaded.review_status == "sent"
    store.close()


def test_sample_item_shows_marker_in_list_line_and_show(tmp_path, monkeypatch, capsys):
    """core/store.py tags an item read through a mock adapter outside `make
    demo` as `_sample` (`Item.is_sample`) - a human working the real queue
    must see that at a glance, in both `list` and `show`."""
    monkeypatch.setenv("AGENT_REPO_ROOT", str(tmp_path))
    settings = _settings()
    store = Store(settings, path=tmp_path / "sample.db")
    item = store.upsert_item(
        "procurement", "sample-marker-1", kind="supply_order",
        payload={"week_start": WEEK_START, "supplier": "Frigo Atlantico",
                "total_eur": 42.0, "has_price_flag": False, "_sample": True})
    assert item.is_sample

    capsys.readouterr()
    review._print_item_line(item, settings.hotel.currency)
    assert "[SAMPLE DATA]" in capsys.readouterr().out

    rc = review.cmd_show(store, argparse.Namespace(id=item.id))
    assert rc == 0
    assert "[SAMPLE DATA]" in capsys.readouterr().out
    store.close()
