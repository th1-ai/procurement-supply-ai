"""The weekly `purchase-note` prompt is deduplicated per week - see
tools/store_ext.py's `purchase_notes` table and tools/run.py::one_pass.

Regression for SIMULATION.md finding 2: with `llm.provider: interactive`,
re-running an already fully-processed week (0 new items, nothing changed)
used to park a brand-new `purchase-note-weekly-<date>.prompt.md` every
single invocation. This proves a second, third, ... run over the same
already-noted week parks NOTHING and needs no fresh interactive answer.

Uses the real repo's data/pending/ (filesystem-based by design - see
core/llm.py) with a week far in the future so fixture ids can never
collide with `make demo` or a real run, and cleans up every file it
creates - same pattern as tests/test_procurement_retry.py.
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

from core.config import (ContactsConfig, HotelConfig, LLMConfig, PrivacyConfig,  # noqa: E402
                         ReviewConfig, Settings, SystemsConfig)
from core.store import Store  # noqa: E402

import run  # noqa: E402
import store_ext  # noqa: E402

AS_OF = "2098-03-02"  # far future: fixture ids never collide with real runs
def _pending():
    """data/pending under the (sandboxed) repo root the code actually uses."""
    from core.config import sub_data_dir
    return sub_data_dir("pending")


def _settings() -> Settings:
    return Settings(
        hotel=HotelConfig(name="Hotel Aurora", rooms=42, currency="EUR", languages=["en"]),
        contacts=ContactsConfig(), systems=SystemsConfig(),
        mode="shadow", llm=LLMConfig(provider="interactive"), review=ReviewConfig(),
        privacy=PrivacyConfig(),
        agent={"subagents": {"supplier_ordering": {"enabled": False, "portal_suppliers": []}},
              "procurement": {"routine_orders": {"autonomy": "draft", "suppliers": [],
                                                 "max_total_eur": 0}}},
        root=REPO_ROOT)


def _cleanup() -> None:
    for f in _pending().glob(f"*{AS_OF}*"):
        f.unlink(missing_ok=True)


@pytest.fixture(autouse=True)
def _isolate():
    _cleanup()
    yield
    _cleanup()


def _answer(task: str, fixture_id: str, payload: dict) -> None:
    (_pending() / f"{task}-{fixture_id}.answer.json").write_text(
        json.dumps(payload), encoding="utf-8")


def test_a_note_already_recorded_for_the_week_is_never_re_asked(tmp_path):
    settings = _settings()
    store = Store(settings, path=tmp_path / "dedup.db")
    store_ext.ensure_schema(store)

    # Pass 1: price-flag pends first (Frigo Atlântico's sea bass, per the
    # bundled fixtures - same catalogue as `make demo`). `one_pass` catches
    # LLMPendingInteractive itself and returns exit code 3 - it never
    # raises out to the caller (see tools/run.py::one_pass).
    code, _ = run.one_pass(settings, store, as_of=AS_OF, limit=20, provider="interactive")
    assert code == 3
    assert (_pending() / f"price-flag-{AS_OF}__frigo-atlantico.prompt.md").exists()

    _answer("price-flag", f"{AS_OF}__frigo-atlantico", {"flag_note": "test flag note"})

    # Pass 2: price-flag resolved, everything else needs no LLM call, so the
    # run reaches the purchase-note step and pends THAT for the first time.
    # AS_OF is far in the future, past every fixture reservation, so only
    # the two covers-driven F&B orders clear zero on-hand (room-driven
    # lines see 0 occupancy) - that is fine, this test is about the note,
    # not the order count.
    code, stats = run.one_pass(settings, store, as_of=AS_OF, limit=20, provider="interactive")
    assert code == 3
    assert stats["processed"] == 2
    note_prompt = _pending() / f"purchase-note-weekly-{AS_OF}.prompt.md"
    assert note_prompt.exists()

    _answer("purchase-note", f"weekly-{AS_OF}", {"note": "Test week note."})

    # Pass 3: everything answered - the week completes, the note is cached.
    code, stats = run.one_pass(settings, store, as_of=AS_OF, limit=20, provider="interactive")
    assert code == 0
    assert stats["processed"] == 0  # every order already left 'new'
    assert stats["skipped"] == 2
    assert store_ext.get_purchase_note(store, AS_OF) == "Test week note."
    assert not note_prompt.exists()  # answered and cleaned up

    # Pass 4 and 5: re-running the same, already-noted week - nothing new -
    # must NEVER park a fresh purchase-note prompt again.
    for _ in range(2):
        code, stats = run.one_pass(settings, store, as_of=AS_OF, limit=20, provider="interactive")
        assert code == 0
        assert stats["processed"] == 0
        assert not note_prompt.exists()
        assert not (_pending() / f"purchase-note-weekly-{AS_OF}.answer.json").exists()

    store.close()
