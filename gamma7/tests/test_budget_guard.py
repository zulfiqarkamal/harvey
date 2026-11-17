"""
Tests for gamma7.engine.budget_guard
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from gamma7.engine.budget_guard import (
    BudgetGuard,
    BudgetExceededError,
    SpendRecord,
    MODEL_PRICING,
    USD_TO_DKK,
)


# --- Basic construction ---

def test_budget_guard_default_limit():
    guard = BudgetGuard(budget_file=pathlib.Path("/dev/null"))
    assert guard.limit_dkk == 200.0


def test_budget_guard_custom_limit():
    guard = BudgetGuard(limit_dkk=50.0, budget_file=pathlib.Path("/dev/null"))
    assert guard.limit_dkk == 50.0


def test_budget_guard_starts_empty():
    guard = BudgetGuard(budget_file=pathlib.Path("/dev/null"))
    assert guard.total_spent_dkk() == 0.0
    assert guard.remaining_dkk() == guard.limit_dkk


# --- Cost calculation ---

def test_cost_calculation_sonnet():
    guard = BudgetGuard(model="claude-sonnet-4-6", budget_file=pathlib.Path("/dev/null"))
    # 1M input tokens at $3/MTok = $3
    # 1M output tokens at $15/MTok = $15 → total $18 × 7 DKK = $126 DKK
    usd, dkk = guard._cost(1_000_000, 1_000_000)
    assert abs(usd - 18.0) < 0.001
    assert abs(dkk - 126.0) < 0.001


def test_cost_calculation_zero_tokens():
    guard = BudgetGuard(budget_file=pathlib.Path("/dev/null"))
    usd, dkk = guard._cost(0, 0)
    assert usd == 0.0
    assert dkk == 0.0


def test_cost_uses_dkk_rate():
    guard = BudgetGuard(model="claude-sonnet-4-6", budget_file=pathlib.Path("/dev/null"))
    usd, dkk = guard._cost(1_000_000, 0)
    assert abs(dkk / usd - USD_TO_DKK) < 0.001


# --- record() ---

def test_record_returns_spend_record(tmp_path):
    guard = BudgetGuard(budget_file=tmp_path / "budget.json")
    rec = guard.record(input_tokens=500, output_tokens=200, label="test")
    assert isinstance(rec, SpendRecord)
    assert rec.label == "test"
    assert rec.input_tokens == 500
    assert rec.output_tokens == 200


def test_record_accumulates_spend(tmp_path):
    guard = BudgetGuard(budget_file=tmp_path / "budget.json")
    guard.record(input_tokens=1_000_000, output_tokens=0, label="a")
    guard.record(input_tokens=1_000_000, output_tokens=0, label="b")
    # 2 × (1M × $3/MTok) × 7 = $42 DKK
    assert guard.total_spent_dkk() > 0
    assert len(guard._records) == 2


# --- check() raises BudgetExceededError ---

def test_check_raises_when_over_limit(tmp_path):
    guard = BudgetGuard(limit_dkk=0.01, budget_file=tmp_path / "budget.json")
    with pytest.raises(BudgetExceededError) as exc_info:
        guard.check(input_tokens=1_000_000, output_tokens=1_000_000)
    assert exc_info.value.limit_dkk == 0.01


def test_check_does_not_raise_within_limit(tmp_path):
    guard = BudgetGuard(limit_dkk=200.0, budget_file=tmp_path / "budget.json")
    guard.check(input_tokens=100, output_tokens=50)  # tiny call, should pass


def test_check_raises_after_accumulated_spend(tmp_path):
    guard = BudgetGuard(limit_dkk=1.0, budget_file=tmp_path / "budget.json")
    guard.record(input_tokens=1_000_000, output_tokens=0)  # ~$3 × 7 = 21 DKK
    with pytest.raises(BudgetExceededError) as exc_info:
        guard.check(input_tokens=100, output_tokens=50)
    assert exc_info.value.spent_dkk > exc_info.value.limit_dkk


def test_budget_exceeded_error_message():
    err = BudgetExceededError(spent_dkk=25.0, limit_dkk=20.0)
    assert "25.00 DKK" in str(err)
    assert "20.00 DKK" in str(err)


# --- persistence ---

def test_record_persists_to_file(tmp_path):
    budget_file = tmp_path / "budget.json"
    guard = BudgetGuard(budget_file=budget_file)
    guard.record(input_tokens=1000, output_tokens=200, label="persist-test")
    assert budget_file.exists()
    data = json.loads(budget_file.read_text())
    assert len(data["records"]) == 1
    assert data["records"][0]["label"] == "persist-test"


def test_load_restores_previous_spend(tmp_path):
    budget_file = tmp_path / "budget.json"
    guard1 = BudgetGuard(budget_file=budget_file)
    guard1.record(input_tokens=500_000, output_tokens=100_000, label="session1")
    spent_after = guard1.total_spent_dkk()

    guard2 = BudgetGuard(budget_file=budget_file)
    assert abs(guard2.total_spent_dkk() - spent_after) < 0.001


def test_load_handles_missing_file(tmp_path):
    guard = BudgetGuard(budget_file=tmp_path / "nonexistent.json")
    assert guard.total_spent_dkk() == 0.0


def test_load_handles_corrupt_file(tmp_path):
    budget_file = tmp_path / "corrupt.json"
    budget_file.write_text("not valid json", encoding="utf-8")
    guard = BudgetGuard(budget_file=budget_file)
    assert guard.total_spent_dkk() == 0.0


# --- get_summary() ---

def test_get_summary_keys(tmp_path):
    guard = BudgetGuard(limit_dkk=200.0, budget_file=tmp_path / "b.json")
    s = guard.get_summary()
    for key in ("limit_dkk", "spent_dkk", "remaining_dkk", "call_count", "model"):
        assert key in s, f"Missing key: {key}"


def test_get_summary_remaining_decreases_after_spend(tmp_path):
    guard = BudgetGuard(limit_dkk=200.0, budget_file=tmp_path / "b.json")
    before = guard.get_summary()["remaining_dkk"]
    guard.record(input_tokens=1_000_000, output_tokens=0)
    after = guard.get_summary()["remaining_dkk"]
    assert after < before


# --- reset() ---

def test_reset_clears_records(tmp_path):
    guard = BudgetGuard(budget_file=tmp_path / "b.json")
    guard.record(input_tokens=1000, output_tokens=200)
    guard.reset()
    assert guard.total_spent_dkk() == 0.0
    assert len(guard._records) == 0


def test_reset_persists_empty_state(tmp_path):
    budget_file = tmp_path / "b.json"
    guard = BudgetGuard(budget_file=budget_file)
    guard.record(input_tokens=1000, output_tokens=200)
    guard.reset()
    guard2 = BudgetGuard(budget_file=budget_file)
    assert guard2.total_spent_dkk() == 0.0
