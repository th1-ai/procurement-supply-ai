"""tools/supply_engine.py - pure decisioning. No store, no adapter, no model -
see docs/how-it-works.md "Deterministic decisioning, LLM for language".
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
for p in (REPO_ROOT, REPO_ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import supply_engine as se  # noqa: E402

FIXTURES = REPO_ROOT / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / "inbound" / f"{name}.json").read_text(encoding="utf-8"))


def _catalog() -> list[dict]:
    return json.loads((FIXTURES / "hotel" / "supply_items.json").read_text(encoding="utf-8"))


def test_demand_signal_ignores_cancelled_reservations():
    reservations = [
        {"check_in": "2026-09-01", "check_out": "2026-09-08", "status": "confirmed"},
        {"check_in": "2026-09-01", "check_out": "2026-09-08", "status": "cancelled"},
    ]
    demand = se.forecast_demand(reservations, [], as_of="2026-09-01", capacity_rooms=42)
    assert demand.room_nights == 7  # only the confirmed one counts


def test_linen_category_uses_room_driven_formula_with_lead_time_cushion():
    item = {"id": "towels", "name": "Towels", "category": "linen", "unit": "pcs",
           "par_level": 999, "on_hand": 0, "daily_use_per_occ_room": 1.0, "supplier": "X",
           "has_portal": True, "unit_cost": 5.0, "baseline_unit_cost": 5.0, "lead_days": 2}
    demand = se.forecast_demand([{"check_in": "2026-09-01", "check_out": "2026-09-08",
                                  "status": "confirmed"}] * 10, [], as_of="2026-09-01",
                                capacity_rooms=42)
    line = se.forecast_line(item, demand, se.DEFAULT_RULES, {})
    # basis = daily_use * avg_daily_rooms * (lead_days + 1) = 1.0 * 10 * 3 = 30
    assert line.basis == 30.0


def test_fnb_category_is_covers_driven_not_room_driven():
    item = {"id": "sea-bass", "name": "Sea bass", "category": "fnb", "unit": "kg",
           "par_level": 999, "on_hand": 0, "daily_use_per_occ_room": 4.6, "supplier": "X",
           "has_portal": False, "unit_cost": 18.5, "baseline_unit_cost": 18.5, "lead_days": 1}
    demand = se.forecast_demand([], [{"covers": 100}], as_of="2026-09-01", capacity_rooms=42)
    line = se.forecast_line(item, demand, se.DEFAULT_RULES, {})
    # perCover = 4.6 / 4.6 = 1.0; basis = 100 covers * 1.0 = 100, regardless of rooms
    assert line.basis == 100.0


def test_waste_guard_caps_perishables_at_105pct_even_with_par_buffer():
    item = {"id": "sea-bass", "name": "Sea bass", "category": "fnb", "unit": "kg",
           "par_level": 999, "on_hand": 0, "daily_use_per_occ_room": 4.6, "supplier": "X",
           "has_portal": False, "unit_cost": 18.5, "baseline_unit_cost": 18.5, "lead_days": 1}
    demand = se.forecast_demand([], [{"covers": 100}], as_of="2026-09-01", capacity_rooms=42)
    line = se.forecast_line(item, demand, se.DEFAULT_RULES, {})
    assert line.capped is True
    assert line.target == 105.0  # basis(100) * 1.05, never the buffered 110


def test_non_perishable_fnb_is_not_capped_by_waste_guard():
    item = {"id": "beef", "name": "Beef", "category": "fnb", "unit": "kg", "par_level": 999,
           "on_hand": 0, "daily_use_per_occ_room": 4.6, "supplier": "X", "has_portal": False,
           "unit_cost": 24.0, "baseline_unit_cost": 24.0, "lead_days": 3}
    demand = se.forecast_demand([], [{"covers": 100}], as_of="2026-09-01", capacity_rooms=42)
    line = se.forecast_line(item, demand, se.DEFAULT_RULES, {})
    assert line.capped is False
    assert line.target == 110.0  # basis(100) * 1.10 par-buffer, no cap for lead_days > 1


def test_occupancy_forecast_off_reverts_to_flat_par_top_up():
    item = {"id": "x", "name": "X", "category": "fnb", "unit": "kg", "par_level": 50,
           "on_hand": 30, "daily_use_per_occ_room": 4.6, "supplier": "X", "has_portal": False,
           "unit_cost": 1.0, "baseline_unit_cost": 1.0, "lead_days": 1}
    demand = se.forecast_demand([], [{"covers": 1000}], as_of="2026-09-01", capacity_rooms=42)
    rules = {**se.DEFAULT_RULES, "occupancy_forecast": False}
    line = se.forecast_line(item, demand, rules, {})
    assert line.qty == 20  # par_level - on_hand, covers/rooms never enter the maths


def test_price_watch_flags_only_above_threshold():
    cheap = {"name": "A", "supplier": "X", "unit": "kg", "unit_cost": 10.0, "baseline_unit_cost": 10.0}
    pricey = {"name": "B", "supplier": "X", "unit": "kg", "unit_cost": 11.0, "baseline_unit_cost": 10.0}
    assert se.price_watch(cheap, 8)[0] is False
    assert se.price_watch(pricey, 8)[0] is True  # 10% above an 8% threshold


def test_zero_occupancy_week_orders_nothing():
    scenario = _load("week-zero-occupancy")
    catalog = _catalog()
    result = se.run_order_proposal(catalog, scenario["reservations"], scenario["covers"], None,
                                   None, as_of=scenario["as_of"],
                                   capacity_rooms=scenario["capacity_rooms"])
    assert result.orders == []
    assert "nothing to order" in result.decision_line


def test_price_creep_fixture_flags_sea_bass():
    scenario = _load("week-price-creep")
    catalog = _catalog()
    for item_id, patch in scenario["catalog_overrides"].items():
        for item in catalog:
            if item["id"] == item_id:
                item.update(patch)
    result = se.run_order_proposal(catalog, scenario["reservations"], scenario["covers"], None,
                                   None, as_of=scenario["as_of"],
                                   capacity_rooms=scenario["capacity_rooms"])
    flagged = [o for o in result.orders if o.has_price_flag]
    assert len(flagged) == 1
    assert flagged[0].supplier == "Frigo Atlântico"


def test_supplier_consolidate_off_gives_one_order_per_line():
    items = [{"id": "a", "name": "A", "category": "other", "unit": "pcs", "par_level": 10,
             "on_hand": 0, "daily_use_per_occ_room": 1.0, "supplier": "X", "has_portal": True,
             "unit_cost": 1.0, "baseline_unit_cost": 1.0, "lead_days": 1},
            {"id": "b", "name": "B", "category": "other", "unit": "pcs", "par_level": 10,
             "on_hand": 0, "daily_use_per_occ_room": 1.0, "supplier": "X", "has_portal": True,
             "unit_cost": 1.0, "baseline_unit_cost": 1.0, "lead_days": 1}]
    reservations = [{"check_in": "2026-09-01", "check_out": "2026-09-08", "status": "confirmed"}] * 5
    on = se.run_order_proposal(items, reservations, [], se.DEFAULT_RULES, {}, as_of="2026-09-01",
                               capacity_rooms=42)
    off = se.run_order_proposal(items, reservations, [], {**se.DEFAULT_RULES,
                                "supplier_consolidate": False}, {}, as_of="2026-09-01",
                               capacity_rooms=42)
    assert len(on.orders) == 1 and len(on.orders[0].lines) == 2
    assert len(off.orders) == 2 and all(len(o.lines) == 1 for o in off.orders)


def test_summarise_waste_splits_at_the_marked_row_and_computes_the_drop():
    rows = [{"day_offset": 0, "waste_eur": 40.0, "note": None},
           {"day_offset": 1, "waste_eur": 60.0, "note": None},
           {"day_offset": 2, "waste_eur": 10.0, "note": "AI took the order book"},
           {"day_offset": 3, "waste_eur": 20.0, "note": None}]
    summary = se.summarise_waste(rows)
    assert summary.before_avg == 50.0
    assert summary.after_avg == 15.0
    assert summary.drop_pct == 70.0


def test_slugify_strips_accents_and_punctuation():
    assert se.slugify("Frigo Atlântico") == "frigo-atlantico"
    assert se.slugify("Linens & Co") == "linens-co"
