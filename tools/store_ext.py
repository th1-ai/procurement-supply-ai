"""tools/store_ext.py - Procurement / Supply AI's own tables, on top of core.store.Store.

The generic ``items`` table (core/store.py) is the review queue: one row per
draft supplier order waiting on a human or a send. It is not a stock ledger.
This module adds the two tables the agent actually needs to query - the SKU
catalogue with its live ``on_hand``, and the daily waste log - plus the pure
helper functions the engine, ``tools/run.py`` and the tests all share.

Call :func:`ensure_schema` once per ``Store`` right after constructing it;
every tool in this repo does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.store import Store, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS supply_items (
  id                     TEXT PRIMARY KEY,
  name                   TEXT NOT NULL,
  category               TEXT NOT NULL,
  unit                   TEXT NOT NULL,
  par_level              REAL NOT NULL DEFAULT 0,
  on_hand                REAL NOT NULL DEFAULT 0,
  canonical_on_hand      REAL NOT NULL DEFAULT 0,
  daily_use_per_occ_room REAL NOT NULL DEFAULT 0,
  supplier               TEXT NOT NULL,
  has_portal             INTEGER NOT NULL DEFAULT 0,
  unit_cost              REAL NOT NULL DEFAULT 0,
  baseline_unit_cost     REAL NOT NULL DEFAULT 0,
  lead_days              INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS waste_log (
  day_offset  INTEGER PRIMARY KEY,
  waste_eur   REAL NOT NULL,
  note        TEXT
);

CREATE TABLE IF NOT EXISTS purchase_notes (
  week_start  TEXT PRIMARY KEY,
  note        TEXT,
  run_id      TEXT,
  updated_at  TEXT NOT NULL
);
"""


def ensure_schema(store: Store) -> None:
    store.migrate(SCHEMA)


# --------------------------------------------------------------------------
# seeding (idempotent: only fills an empty table)
# --------------------------------------------------------------------------
def seed_catalog(store: Store, path: Path) -> int:
    """Load ``supply_items.json`` once. Returns rows inserted (0 if already seeded)."""
    if store.db.execute("SELECT COUNT(*) AS n FROM supply_items").fetchone()["n"]:
        return 0
    if not Path(path).exists():
        return 0
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    for row in rows:
        on_hand = float(row.get("on_hand", 0))
        store.db.execute(
            "INSERT INTO supply_items (id, name, category, unit, par_level, on_hand, "
            "canonical_on_hand, daily_use_per_occ_room, supplier, has_portal, unit_cost, "
            "baseline_unit_cost, lead_days) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["name"], row["category"], row["unit"],
             float(row.get("par_level", 0)), on_hand, on_hand,
             float(row.get("daily_use_per_occ_room", 0)), row["supplier"],
             1 if row.get("has_portal") else 0, float(row["unit_cost"]),
             float(row.get("baseline_unit_cost", row["unit_cost"])),
             int(row.get("lead_days", 0))))
    return len(rows)


def seed_waste_log(store: Store, path: Path) -> int:
    if store.db.execute("SELECT COUNT(*) AS n FROM waste_log").fetchone()["n"]:
        return 0
    if not Path(path).exists():
        return 0
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    for row in rows:
        store.db.execute(
            "INSERT OR IGNORE INTO waste_log (day_offset, waste_eur, note) VALUES (?,?,?)",
            (int(row["day_offset"]), float(row["waste_eur"]), row.get("note")))
    return len(rows)


# --------------------------------------------------------------------------
# reads
# --------------------------------------------------------------------------
def get_catalog(store: Store) -> list[dict]:
    rows = store.db.execute("SELECT * FROM supply_items ORDER BY category, name").fetchall()
    return [dict(r) for r in rows]


def is_seeded(store: Store, table: str) -> bool:
    row = store.db.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
    return bool(row and row["n"])


def load_json_fixture(path: Path) -> list[dict]:
    """Read a fixture file straight off disk - no store, no write, ever.

    Used by ``--dry-run`` on a fresh clone: it must compute a real preview
    without seeding a single row (see docs/how-it-works.md "Idempotency").
    """
    path = Path(path)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def get_waste_rows(store: Store) -> list[dict]:
    rows = store.db.execute("SELECT * FROM waste_log ORDER BY day_offset ASC").fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------
def apply_delivery(store: Store, order_payload: dict) -> None:
    """Increment ``on_hand`` per line. Called once, when an order is marked delivered."""
    for line in order_payload.get("lines", []):
        store.db.execute(
            "UPDATE supply_items SET on_hand = on_hand + ? WHERE id = ?",
            (float(line["qty"]), line["item_id"]))


def has_purchase_note(store: Store, week_start: str) -> bool:
    """Has this week already got a purchase-note narrative? See
    ``tools/run.py::one_pass`` - a re-run with nothing new to report never
    parks a fresh ``purchase-note`` prompt once this is true (spec finding:
    the weekly note must be deduplicated per week, not re-asked every pass).
    """
    row = store.db.execute(
        "SELECT 1 AS n FROM purchase_notes WHERE week_start = ?", (week_start,)).fetchone()
    return row is not None


def get_purchase_note(store: Store, week_start: str) -> str | None:
    row = store.db.execute(
        "SELECT note FROM purchase_notes WHERE week_start = ?", (week_start,)).fetchone()
    return row["note"] if row else None


def save_purchase_note(store: Store, week_start: str, note: str | None, run_id: str) -> None:
    now = utcnow()
    store.db.execute(
        "INSERT INTO purchase_notes (week_start, note, run_id, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(week_start) DO UPDATE SET note=excluded.note, run_id=excluded.run_id, "
        "updated_at=excluded.updated_at",
        (week_start, note, run_id, now))


def record_run_narrative(store: Store, run_id: str, note: str | None) -> None:
    """The purchase-note is cosmetic - store it on the run's own stats, not on an item.

    Read-modify-write in Python rather than a SQL JSON function: stdlib
    ``sqlite3`` does not guarantee the JSON1 extension is compiled in.
    """
    row = store.db.execute("SELECT stats_json FROM runs WHERE id = ?", (run_id,)).fetchone()
    stats: dict[str, Any] = {}
    if row and row["stats_json"]:
        try:
            stats = json.loads(row["stats_json"])
        except (TypeError, ValueError):
            stats = {}
    stats["narrative"] = note
    store.db.execute("UPDATE runs SET stats_json = ? WHERE id = ?",
                     (json.dumps(stats, ensure_ascii=False), run_id))
