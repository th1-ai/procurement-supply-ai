"""Every human-facing money figure formats with `hotel.currency` - never a
hardcoded "EUR". Regression for SIMULATION.md finding 1: a Norwegian
persona (`currency: NOK`) hit "EUR" in the thinking log, the decision
line, the purchase-note narrative and the review queue, even though
`core/templates.py` already threaded `hotel.currency` into the LLM
prompt's system block correctly.
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
import supply_engine as se  # noqa: E402

AS_OF = "2026-09-01"  # matches fixtures/hotel/{reservations,covers,supply_items}.json


def _nok_settings() -> Settings:
    agent = {"subagents": {"supplier_ordering": {"enabled": False, "portal_suppliers": []}},
            "procurement": {"routine_orders": {"autonomy": "draft", "suppliers": [],
                                               "max_total_eur": 0}}}
    return Settings(
        hotel=HotelConfig(name="Fjellstua Lodge", rooms=30, currency="NOK",
                          languages=["no", "en"]),
        contacts=ContactsConfig(),
        systems=SystemsConfig(pms=AdapterConfig("mock"), email=AdapterConfig("mock"),
                              messaging=AdapterConfig("mock"), sheets=AdapterConfig("csv")),
        mode="shadow", llm=LLMConfig(provider="mock"), review=ReviewConfig(),
        privacy=PrivacyConfig(), agent=agent, root=REPO_ROOT)


def test_supply_engine_never_hardcodes_eur():
    catalog = [{"id": "x", "name": "X", "category": "fnb", "unit": "kg", "par_level": 999,
               "on_hand": 0, "daily_use_per_occ_room": 4.6, "supplier": "X", "has_portal": False,
               "unit_cost": 20.0, "baseline_unit_cost": 10.0, "lead_days": 1}]
    reservations = [{"check_in": "2026-09-01", "check_out": "2026-09-08", "status": "confirmed"}]
    result = se.run_order_proposal(catalog, reservations, [{"covers": 50}], se.DEFAULT_RULES, {},
                                   as_of="2026-09-01", capacity_rooms=42, currency="NOK")
    full_text = "\n".join(result.thinking_log) + result.decision_line
    assert "NOK" in full_text
    assert "EUR" not in full_text

    note = se.narrate_run(result, "Fjellstua Lodge", currency="NOK")
    assert "NOK" in note
    assert "EUR" not in note

    waste = se.summarise_waste(
        [{"day_offset": 0, "waste_eur": 40.0, "note": None},
         {"day_offset": 1, "waste_eur": 10.0, "note": "AI took the order book"},
         {"day_offset": 2, "waste_eur": 5.0, "note": None}],
        currency="NOK")
    assert "NOK" in waste.note
    assert "EUR" not in waste.note


def test_one_pass_prints_nok_not_eur_throughout(tmp_path, capsys):
    settings = _nok_settings()
    store = Store(settings, path=tmp_path / "nok.db")
    store_ext.ensure_schema(store)

    code, stats = run.one_pass(settings, store, as_of=AS_OF, limit=20, provider="mock")
    out = capsys.readouterr().out

    assert code == 0
    assert stats["processed"] == 4
    assert "NOK" in out  # the purchase-note narrative, at least
    assert "EUR" not in out
    store.close()


def test_dry_run_preview_uses_the_hotels_currency(tmp_path, capsys):
    settings = _nok_settings()
    settings.dry_run = True
    store = Store(settings, path=tmp_path / "nok_dry.db")
    store_ext.ensure_schema(store)

    run.one_pass(settings, store, as_of=AS_OF, limit=20, provider="mock")
    out = capsys.readouterr().out

    assert "NOK" in out
    assert "EUR" not in out
    store.close()
