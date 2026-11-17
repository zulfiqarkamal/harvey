"""
Gamma7 BudgetGuard — tracks Anthropic API spend and enforces a DKK limit.

Exchange rate: 1 USD = 7 DKK (fixed)
Model pricing (claude-sonnet-4-6): $3.00/MTok input, $15.00/MTok output

Default limit: 200 DKK (~$28). When reached, raises BudgetExceededError.
Spend is persisted to data/api_budget.json so it survives restarts.
"""

from __future__ import annotations

import datetime
import json
import pathlib
from dataclasses import asdict, dataclass
from typing import Optional

USD_TO_DKK = 7.0

MODEL_PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-4-6": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
    "claude-sonnet-4-5": {"input_per_mtok": 3.0, "output_per_mtok": 15.0},
    "claude-haiku-5-5":  {"input_per_mtok": 0.8, "output_per_mtok": 4.0},
}

DEFAULT_LIMIT_DKK = 200.0
DEFAULT_BUDGET_FILE = pathlib.Path(__file__).parent.parent.parent / "data" / "api_budget.json"


@dataclass
class SpendRecord:
    timestamp: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cost_dkk: float
    label: str


class BudgetExceededError(Exception):
    """Raised when a pending LLM call would exceed the configured DKK limit."""

    def __init__(self, spent_dkk: float, limit_dkk: float) -> None:
        self.spent_dkk = spent_dkk
        self.limit_dkk = limit_dkk
        super().__init__(
            f"Budget limit reached: {spent_dkk:.2f} DKK spent of {limit_dkk:.2f} DKK limit. "
            f"Approve additional budget before continuing."
        )


class BudgetGuard:
    """
    Tracks Anthropic API spend across sessions and enforces a DKK ceiling.

    Usage:
        guard = BudgetGuard()
        guard.check(input_tokens=500, output_tokens=200)   # raises if over limit
        guard.record(input_tokens=500, output_tokens=200, label="overlay:ART30-001")
    """

    def __init__(
        self,
        limit_dkk: float = DEFAULT_LIMIT_DKK,
        model: str = "claude-sonnet-4-6",
        budget_file: Optional[pathlib.Path] = None,
    ) -> None:
        self.limit_dkk = limit_dkk
        self.model = model
        self._file = pathlib.Path(budget_file) if budget_file else DEFAULT_BUDGET_FILE
        self._records: list[SpendRecord] = []
        self._load()

    # --- persistence ---

    def _load(self) -> None:
        if self._file.exists():
            try:
                raw = json.loads(self._file.read_text(encoding="utf-8"))
                self._records = [SpendRecord(**r) for r in raw.get("records", [])]
            except (json.JSONDecodeError, TypeError, KeyError):
                self._records = []
        else:
            self._records = []

    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "limit_dkk": self.limit_dkk,
            "model": self.model,
            "records": [asdict(r) for r in self._records],
        }
        self._file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- cost calculation ---

    def _cost(self, input_tokens: int, output_tokens: int) -> tuple[float, float]:
        """Returns (cost_usd, cost_dkk)."""
        pricing = MODEL_PRICING.get(self.model, MODEL_PRICING["claude-sonnet-4-6"])
        cost_usd = (
            (input_tokens / 1_000_000) * pricing["input_per_mtok"]
            + (output_tokens / 1_000_000) * pricing["output_per_mtok"]
        )
        return cost_usd, cost_usd * USD_TO_DKK

    # --- public API ---

    def total_spent_dkk(self) -> float:
        return sum(r.cost_dkk for r in self._records)

    def remaining_dkk(self) -> float:
        return max(0.0, self.limit_dkk - self.total_spent_dkk())

    def check(self, input_tokens: int = 1000, output_tokens: int = 300) -> None:
        """
        Raise BudgetExceededError if this call would push total spend past the limit.
        Call this BEFORE making an API request.
        """
        _, call_dkk = self._cost(input_tokens, output_tokens)
        if self.total_spent_dkk() + call_dkk > self.limit_dkk:
            raise BudgetExceededError(self.total_spent_dkk(), self.limit_dkk)

    def record(self, input_tokens: int, output_tokens: int, label: str = "") -> SpendRecord:
        """Record actual spend from a completed API call and persist."""
        cost_usd, cost_dkk = self._cost(input_tokens, output_tokens)
        rec = SpendRecord(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost_usd, 6),
            cost_dkk=round(cost_dkk, 4),
            label=label,
        )
        self._records.append(rec)
        self._save()
        return rec

    def get_summary(self) -> dict:
        spent = self.total_spent_dkk()
        return {
            "limit_dkk": self.limit_dkk,
            "spent_dkk": round(spent, 2),
            "remaining_dkk": round(self.remaining_dkk(), 2),
            "call_count": len(self._records),
            "total_input_tokens": sum(r.input_tokens for r in self._records),
            "total_output_tokens": sum(r.output_tokens for r in self._records),
            "model": self.model,
            "budget_file": str(self._file),
        }

    def reset(self) -> None:
        """Clear all spend records (use for testing or after Zulfiqar approves a new budget)."""
        self._records = []
        self._save()
