"""tools/supply_engine.py - the Quartermaster's decisioning. Pure functions, no I/O.

Mirrors the demo platform's `supply-engine.ts` (`runOrderProposal`,
`summariseWaste`): the page (here, ``tools/run.py``) hands this module rows
it already fetched, and gets back visible thinking steps plus one draft
purchase order per supplier, with the arithmetic written into every line's
reason string. Nothing here calls a model, a store or an adapter - see
docs/how-it-works.md.

    from supply_engine import forecast_demand, run_order_proposal
    demand = forecast_demand(reservations, covers, as_of="2026-09-01", capacity_rooms=42)
    result = run_order_proposal(catalog, reservations, covers, rules, params,
                                as_of="2026-09-01", capacity_rooms=42)
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

DEFAULT_HORIZON_DAYS = 7
DEFAULT_COVERS_PER_OCC_ROOM = 4.6
DEFAULT_PAR_BUFFER_PCT = 10
DEFAULT_WASTE_CAP_PCT = 105
DEFAULT_PRICE_THRESHOLD_PCT = 8

DEFAULT_RULES = {
    "occupancy_forecast": True,
    "waste_guard": True,
    "par_buffer": True,
    "supplier_consolidate": True,
    "price_watch": True,
}


def slugify(text: str) -> str:
    """ASCII, lowercase, hyphenated - used for supplier slugs in external_id."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or "supplier"


def _parse(d: str) -> date:
    return date.fromisoformat(d[:10])


def window(as_of: str, horizon_days: int = DEFAULT_HORIZON_DAYS) -> tuple[date, date]:
    """Return (start, end_exclusive) - ``horizon_days`` nights starting at ``as_of``."""
    start = _parse(as_of)
    return start, start + timedelta(days=horizon_days)


def nights_overlap(check_in: str, check_out: str, start: date, end_exclusive: date) -> int:
    """How many nights of ``[check_in, check_out)`` fall inside ``[start, end_exclusive)``."""
    try:
        ci, co = _parse(check_in), _parse(check_out)
    except ValueError:
        return 0
    lo, hi = max(ci, start), min(co, end_exclusive)
    return max(0, (hi - lo).days)


@dataclass
class DemandSignal:
    """The week's demand: §3 step 2 of specs/procurement-supply-ai.md."""

    as_of: str
    horizon_days: int
    capacity_rooms: int
    room_nights: int
    capacity_room_nights: int
    avg_daily_rooms: float
    covers_total: int
    pct_capacity: float
    window_end: str

    def step_text(self) -> str:
        end = _parse(self.window_end) - timedelta(days=1)
        return (f"{self.room_nights} occupied room-night(s) on the books across the next "
               f"{self.horizon_days} nights ({self.pct_capacity:.0f}% of capacity, "
               f"{self.avg_daily_rooms:.1f} rooms a night), plus {self.covers_total} "
               f"restaurant cover(s) already booked through {end.isoformat()}.")


def forecast_demand(reservations: list[dict], covers_rows: list[dict], *, as_of: str,
                    capacity_rooms: int, horizon_days: int = DEFAULT_HORIZON_DAYS) -> DemandSignal:
    """§3 step 2: room-nights, average daily rooms, and total covers over the horizon."""
    start, end = window(as_of, horizon_days)
    room_nights = sum(
        nights_overlap(r.get("check_in", ""), r.get("check_out", ""), start, end)
        for r in reservations if (r.get("status") or "confirmed") not in ("cancelled", "no_show"))
    covers_total = sum(int(c.get("covers", 0)) for c in covers_rows)
    capacity_room_nights = capacity_rooms * horizon_days
    pct = (room_nights / capacity_room_nights * 100) if capacity_room_nights else 0.0
    return DemandSignal(
        as_of=as_of, horizon_days=horizon_days, capacity_rooms=capacity_rooms,
        room_nights=room_nights, capacity_room_nights=capacity_room_nights,
        avg_daily_rooms=round(room_nights / horizon_days, 2) if horizon_days else 0.0,
        covers_total=covers_total, pct_capacity=round(pct, 1), window_end=end.isoformat())


def price_watch(item: dict, threshold_pct: float = DEFAULT_PRICE_THRESHOLD_PCT,
                currency: str = "EUR") -> tuple[bool, str]:
    """§3 step 7: flag a line more than ``threshold_pct`` above its 90-day baseline."""
    baseline = float(item.get("baseline_unit_cost") or item.get("unit_cost") or 0)
    current = float(item.get("unit_cost") or 0)
    if baseline <= 0:
        return False, ""
    pct_above = (current - baseline) / baseline * 100
    if pct_above <= threshold_pct:
        return False, ""
    detail = (f"{item['name']} from {item['supplier']} is {currency} {current:.2f}/{item['unit']}, "
             f"{pct_above:.0f}% above its 90-day baseline of {currency} {baseline:.2f}/{item['unit']}.")
    return True, detail


@dataclass
class LineForecast:
    """One SKU's forecast - §3 step 3. ``reason`` is the arithmetic, in words."""

    item_id: str
    name: str
    category: str
    supplier: str
    has_portal: bool
    unit: str
    on_hand: float
    basis: float
    target: float
    capped: bool
    trimmed_units: float
    qty: float
    unit_cost: float
    lead_days: int
    perishable: bool
    price_flagged: bool
    price_flag_detail: str
    reason: str

    @property
    def line_total(self) -> float:
        return round(self.qty * self.unit_cost, 2)

    def as_dict(self) -> dict:
        return {"item_id": self.item_id, "name": self.name, "category": self.category,
                "supplier": self.supplier, "unit": self.unit, "qty": self.qty,
                "unit_cost": self.unit_cost, "line_total": self.line_total,
                "reason": self.reason, "price_flagged": self.price_flagged,
                "price_flag_detail": self.price_flag_detail}


def forecast_line(item: dict, demand: DemandSignal, rules: dict, params: dict,
                  currency: str = "EUR") -> LineForecast:
    """§3 step 3: the per-line basis, the par-buffer cushion, and the waste guard cap.

    ``rules["occupancy_forecast"] is False`` reverts to the naive branch: qty
    is just ``par_level - on_hand`` with no demand in the maths at all (spec
    section 3 step 3, "the naive line").
    """
    covers_per_room = params.get("covers_per_occ_room", DEFAULT_COVERS_PER_OCC_ROOM)
    par_buffer_pct = params.get("par_buffer_pct", DEFAULT_PAR_BUFFER_PCT)
    waste_cap_pct = params.get("waste_cap_pct", DEFAULT_WASTE_CAP_PCT)
    price_threshold = params.get("price_watch_threshold_pct", DEFAULT_PRICE_THRESHOLD_PCT)

    category = item["category"]
    on_hand = float(item.get("on_hand", 0))
    daily_use = float(item.get("daily_use_per_occ_room", 0))
    lead_days = int(item.get("lead_days", 0))
    perishable = category == "fnb" and lead_days <= 1
    flagged, flag_detail = (price_watch(item, price_threshold, currency)
                            if rules.get("price_watch", True) else (False, ""))

    if not rules.get("occupancy_forecast", True):
        par_level = float(item.get("par_level", 0))
        qty = max(0.0, math.ceil(par_level - on_hand))
        reason = (f"'Order to the forecast' is off: par level {par_level:g} {item['unit']} - "
                 f"{on_hand:g} on hand = order {qty:g}. The occupancy book and covers sheet "
                 f"are not used.")
        return LineForecast(item["id"], item["name"], category, item["supplier"],
                            bool(item.get("has_portal")), item["unit"],
                            on_hand, basis=0.0, target=qty, capped=False, trimmed_units=0.0,
                            qty=qty, unit_cost=float(item["unit_cost"]), lead_days=lead_days,
                            perishable=perishable, price_flagged=flagged,
                            price_flag_detail=flag_detail, reason=reason)

    if category == "linen":
        basis = daily_use * demand.avg_daily_rooms * (lead_days + 1)
        formula = (f"{daily_use:g} per occupied room x {demand.avg_daily_rooms:g} rooms/night x "
                  f"({lead_days} lead day(s) + 1) = {basis:.1f} {item['unit']}")
    elif category == "fnb":
        per_cover = daily_use / covers_per_room if covers_per_room else 0.0
        basis = demand.covers_total * per_cover
        formula = (f"{demand.covers_total} covers booked x {per_cover:.4f} per cover "
                  f"({daily_use:g} per occupied room / {covers_per_room:g} covers per room) "
                  f"= {basis:.1f} {item['unit']}")
    else:
        basis = daily_use * demand.room_nights
        formula = f"{daily_use:g} per occupied room x {demand.room_nights} room-nights = {basis:.1f} {item['unit']}"

    target = basis
    reason_parts = [formula]
    if rules.get("par_buffer", True):
        target = basis * (1 + par_buffer_pct / 100)
        reason_parts.append(f"{par_buffer_pct:g}% cushion = {target:.1f}")

    capped, trimmed = False, 0.0
    if rules.get("waste_guard", True) and perishable:
        cap = basis * (waste_cap_pct / 100)
        if target > cap:
            trimmed = round(target - cap, 2)
            target = cap
            capped = True
            reason_parts.append(f"waste guard caps a perishable at {waste_cap_pct:g}% of "
                                f"forecast ({cap:.1f}), trimming {trimmed:g} {item['unit']}")

    qty = max(0.0, math.ceil(target - on_hand))
    reason_parts.append(f"{on_hand:g} on hand -> order {qty:g}")
    reason = "; ".join(reason_parts) + "."
    if flagged:
        reason += f" Price flagged: {flag_detail}"

    return LineForecast(item["id"], item["name"], category, item["supplier"],
                        bool(item.get("has_portal")), item["unit"],
                        on_hand, basis=round(basis, 2), target=round(target, 2), capped=capped,
                        trimmed_units=trimmed, qty=qty, unit_cost=float(item["unit_cost"]),
                        lead_days=lead_days, perishable=perishable, price_flagged=flagged,
                        price_flag_detail=flag_detail, reason=reason)


@dataclass
class SupplierOrder:
    """One draft purchase order - what becomes an ``items`` row in tools/run.py."""

    supplier: str
    lines: list[LineForecast]
    reason_summary: str = ""

    @property
    def total_eur(self) -> float:
        return round(sum(l.line_total for l in self.lines), 2)

    @property
    def has_price_flag(self) -> bool:
        return any(l.price_flagged for l in self.lines)

    @property
    def perishable_only(self) -> bool:
        return all(l.perishable for l in self.lines)

    @property
    def has_portal(self) -> bool:
        """Whether this supplier has an online ordering system (all lines share one supplier)."""
        return bool(self.lines) and self.lines[0].has_portal

    def as_dict(self) -> dict:
        return {"supplier": self.supplier, "lines": [l.as_dict() for l in self.lines],
                "total_eur": self.total_eur, "reason_summary": self.reason_summary,
                "has_price_flag": self.has_price_flag, "has_portal": self.has_portal}


def build_orders(lines: list[LineForecast], rules: dict) -> list[SupplierOrder]:
    """§3 step 6: one PO per supplier (``supplier_consolidate`` on) or one per line (off)."""
    ordered = [l for l in lines if l.qty > 0]
    if rules.get("supplier_consolidate", True):
        by_supplier: dict[str, list[LineForecast]] = {}
        for line in ordered:
            by_supplier.setdefault(line.supplier, []).append(line)
        orders = []
        for supplier, supplier_lines in by_supplier.items():
            names = ", ".join(f"{l.qty:g} {l.unit} {l.name}" for l in supplier_lines[:4])
            more = f" and {len(supplier_lines) - 4} more line(s)" if len(supplier_lines) > 4 else ""
            orders.append(SupplierOrder(supplier, supplier_lines,
                                        reason_summary=f"{supplier}: {names}{more}."))
        return orders
    return [SupplierOrder(l.supplier, [l],
                          reason_summary=f"{l.supplier} delivers once for this line alone - "
                          f"supplier-consolidate is off.") for l in ordered]


def naive_total(items: list[dict]) -> float:
    """Total value of a flat par-level top-up, ignoring demand entirely - the comparison line."""
    total = 0.0
    for item in items:
        qty = max(0.0, math.ceil(float(item.get("par_level", 0)) - float(item.get("on_hand", 0))))
        total += qty * float(item["unit_cost"])
    return round(total, 2)


@dataclass
class ProposalResult:
    demand: DemandSignal
    thinking_log: list[str]
    orders: list[SupplierOrder]
    total_eur: float
    naive_total_eur: float
    decision_line: str


def run_order_proposal(items: list[dict], reservations: list[dict], covers_rows: list[dict],
                       rules: dict | None, params: dict | None, *, as_of: str,
                       capacity_rooms: int, horizon_days: int = DEFAULT_HORIZON_DAYS,
                       currency: str = "EUR") -> ProposalResult:
    """The whole run: §3 steps 2-8. Pure - no store, no adapter, no model."""
    rules = {**DEFAULT_RULES, **(rules or {})}
    params = params or {}
    demand = forecast_demand(reservations, covers_rows, as_of=as_of, capacity_rooms=capacity_rooms,
                             horizon_days=horizon_days)
    log = [demand.step_text() if rules.get("occupancy_forecast", True) else
          "'Order to the forecast' is off - the occupancy book and covers sheet are ignored; "
          "every line goes flat to par."]

    lines = [forecast_line(item, demand, rules, params, currency) for item in items]
    trimmed = [l for l in lines if l.capped]
    if rules.get("waste_guard", True) and rules.get("occupancy_forecast", True):
        if trimmed:
            saved = round(sum(l.trimmed_units * l.unit_cost for l in trimmed), 2)
            log.append(f"Waste guard trimmed {len(trimmed)} perishable line(s), saving about "
                      f"{currency} {saved:.2f} this week: " +
                      "; ".join(f"{l.name} by {l.trimmed_units:g} {l.unit}" for l in trimmed) + ".")
        else:
            log.append("Waste guard: nothing needed trimming this week.")
    elif rules.get("occupancy_forecast", True):
        would_trim = []
        for item in items:
            perishable = item["category"] == "fnb" and int(item.get("lead_days", 0)) <= 1
            if not perishable:
                continue
            per_cover = item["daily_use_per_occ_room"] / params.get(
                "covers_per_occ_room", DEFAULT_COVERS_PER_OCC_ROOM)
            basis = demand.covers_total * per_cover
            cap = basis * (params.get("waste_cap_pct", DEFAULT_WASTE_CAP_PCT) / 100)
            buffered = basis * (1 + params.get("par_buffer_pct", DEFAULT_PAR_BUFFER_PCT) / 100)
            if buffered > cap:
                would_trim.append(f"{item['name']} by {buffered - cap:.1f} {item['unit']}")
        log.append("Waste guard is off - this is how the Sunday fruit mountain used to happen. "
                  "With it on, this run would have trimmed: " +
                  ("; ".join(would_trim) + "." if would_trim else "nothing."))
    else:
        log.append("Waste guard has nothing to cap against with the forecast off.")

    if rules.get("price_watch", True):
        flagged = [l for l in lines if l.price_flagged]
        if flagged:
            log.append(f"Price watch: {len(flagged)} line(s) checked against their 90-day "
                      f"baseline - flagged: " + "; ".join(l.price_flag_detail for l in flagged))
        else:
            log.append(f"All {len(lines)} unit costs checked against their 90-day baseline - "
                      f"none is more than {params.get('price_watch_threshold_pct', DEFAULT_PRICE_THRESHOLD_PCT):g}% above it.")
    else:
        log.append("Price watch is off - costs go through unchecked.")

    orders = build_orders(lines, rules)
    for order in orders:
        log.append(order.reason_summary)

    total = round(sum(o.total_eur for o in orders), 2)
    naive = naive_total(items)
    delta = round(naive - total, 2)
    if not orders:
        decision = "No lines cleared zero on-hand this week - nothing to order."
    elif rules.get("occupancy_forecast", True):
        word = "less" if delta >= 0 else "more"
        decision = (f"{currency} {total:.2f} across {len(orders)} order(s) for "
                   f"{demand.room_nights} room-nights and {demand.covers_total} covers - "
                   f"{currency} {abs(delta):.2f} {word} than a flat par top-up would have "
                   f"bought.")
    else:
        decision = (f"{currency} {total:.2f} across {len(orders)} order(s) - {currency} "
                   f"{abs(delta):.2f} more than the forecast asks for.")
    log.append(decision)

    return ProposalResult(demand=demand, thinking_log=log, orders=orders, total_eur=total,
                          naive_total_eur=naive, decision_line=decision)


@dataclass
class WasteSummary:
    before_avg: float
    after_avg: float
    drop_pct: float
    annualised_saving: float
    note: str


def summarise_waste(rows: list[dict], currency: str = "EUR") -> WasteSummary | None:
    """§3 step 12: split at the first row carrying a note, average each half."""
    if not rows:
        return None
    split = next((i for i, r in enumerate(rows) if r.get("note")), None)
    if split is None or split == 0 or split == len(rows) - 1:
        return None
    before = [r["waste_eur"] for r in rows[:split]]
    after = [r["waste_eur"] for r in rows[split:]]
    before_avg = round(sum(before) / len(before), 2)
    after_avg = round(sum(after) / len(after), 2)
    drop_pct = round((before_avg - after_avg) / before_avg * 100, 1) if before_avg else 0.0
    annualised = round((before_avg - after_avg) * 365, 2)
    note = (f"Waste down {drop_pct:g}% since the AI took the order book - {currency} "
           f"{before_avg:.2f} a day before, {currency} {after_avg:.2f} a day since. That is "
           f"{currency} {annualised:.2f} a year that used to go in the bin.")
    return WasteSummary(before_avg, after_avg, drop_pct, annualised, note)


def narrate_run(result: ProposalResult, hotel_name: str, currency: str = "EUR") -> str:
    """A 2-4 sentence morning note, computed straight from ``result`` - the
    ``llm.provider: mock`` narrative (``make demo``, ``make test``).

    Mirrors what ``prompts/purchase-note.md`` asks a real model to write -
    headline, demand, waste-guard trim, comparison to a flat top-up - using
    only facts already computed in ``result``, so it can never invent a
    supplier, an item or a number, and it can never go stale: it always
    describes whatever catalogue and demand are actually loaded, unlike a
    canned fixture pinned to one property's sample data.
    """
    if not result.orders:
        return (f"Nothing cleared its on-hand for {hotel_name} this week - "
               f"{result.demand.step_text()} No orders to place.")

    suppliers = [o.supplier for o in result.orders]
    supplier_word = "supplier" if len(suppliers) == 1 else "suppliers"
    sentences = [
        f"This run orders from {len(suppliers)} {supplier_word} "
        f"({', '.join(suppliers)}) for {currency} {result.total_eur:.2f}.",
        result.demand.step_text(),
    ]

    flagged = [l for order in result.orders for l in order.lines if l.price_flagged]
    if flagged:
        sentences.append("Price flagged: " + "; ".join(l.price_flag_detail for l in flagged))

    trimmed = [l for order in result.orders for l in order.lines if l.capped]
    if trimmed:
        bits = "; ".join(f"{l.name} by {l.trimmed_units:g} {l.unit}" for l in trimmed)
        sentences.append(f"The waste guard trimmed {len(trimmed)} perishable line(s) back to "
                         f"forecast: {bits}.")

    delta = round(result.naive_total_eur - result.total_eur, 2)
    word = "less" if delta >= 0 else "more"
    sentences.append(f"That is {currency} {abs(delta):.2f} {word} than a flat par-level "
                     f"top-up would have bought this week.")
    return " ".join(sentences)


def load_covers(fixtures_dir: Path) -> list[dict]:
    """Restaurant covers have no adapter - read straight from the fixtures/import file.

    See docs/how-it-works.md design decision 1.
    """
    path = Path(fixtures_dir) / "covers.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def is_routine(order: SupplierOrder, routine_suppliers: list[str], max_total_eur: float) -> bool:
    """Eligible for the one auto-send lane - see docs/how-it-works.md design decision 6."""
    return (order.supplier in routine_suppliers and not order.has_price_flag
           and order.total_eur <= max_total_eur)


def routine_order_message(order: SupplierOrder, hotel_name: str) -> str:
    """Fixed template - never LLM-authored, because nothing reviews it before it sends."""
    lines = "; ".join(f"{l.qty:g} {l.unit} {l.name}" for l in order.lines)
    return (f"Good morning, please deliver the following as soon as your normal lead time "
           f"allows: {lines}. Thank you, {hotel_name}.")
