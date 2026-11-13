"""
Gamma7 — Precision/recall benchmark.

Runs the heuristic gap analysis engine against:
  1. 5 in-sample contracts (same contracts used to tune match_terms)
  2. 2 out-of-sample holdout contracts (not seen during tuning)

Reports precision, recall, and F1 for each set separately, so users can
distinguish in-sample fit from independently verified generalisation.

Usage:
    python run_benchmark.py                 # heuristic only
    python run_benchmark.py --holdout-only  # holdout set only (faster)

LLM overlay (requires ANTHROPIC_API_KEY):
    python run_benchmark.py --llm           # compare heuristic vs LLM-enhanced
    Currently scaffolded — not yet implemented; prints a placeholder message.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))          # gamma7 package
sys.path.insert(0, str(REPO_ROOT.parent))   # repo root (for gamma7.*)

from gamma7.engine.analyser import ContractAnalyser, ContractMetadata, validate_against_expected
from gamma7.engine.budget_guard import BudgetGuard, BudgetExceededError
from gamma7.engine.llm_overlay import run_overlay, run_evidence_validation_overlay

CONTRACTS_DIR = REPO_ROOT / "synthetic_contracts"
OUTPUT_DIR = REPO_ROOT / "engine" / "output"

INSAMPLE_EXPECTED_FILE = CONTRACTS_DIR / "expected_findings.yaml"
HOLDOUT_EXPECTED_FILE = CONTRACTS_DIR / "expected_findings_holdout.yaml"

IN_SAMPLE_CONTRACTS = [
    {"file": "contract_01_cloudhost.md", "key": "contract_01_cloudhost",
     "classification": "critical_important", "supplier": "NordCloud Hosting A/S",
     "customer": "Nordbank Sparekasse"},
    {"file": "contract_02_payment.md", "key": "contract_02_payment",
     "classification": "critical_important", "supplier": "PayLink Technologies Ltd",
     "customer": "Fjord Finans A/S"},
    {"file": "contract_03_legacy.md", "key": "contract_03_legacy",
     "classification": "non_critical", "supplier": "DataCenter DK ApS",
     "customer": "Kapital Forsikring A/S"},
    {"file": "contract_04_saas.md", "key": "contract_04_saas",
     "classification": "non_critical", "supplier": "ComplianceTools International BV",
     "customer": "Coastal Payment Institution A/S"},
    {"file": "contract_05_good.md", "key": "contract_05_good",
     "classification": "critical_important", "supplier": "SecureVault Nordic A/S",
     "customer": "Nordisk Bank & Invest A/S"},
]

HOLDOUT_CONTRACTS = [
    {"file": "contract_06_modern_fintech.md", "key": "contract_06_modern_fintech",
     "classification": "critical_important", "supplier": "CloudCore Infrastructure ApS",
     "customer": "NordicPay Solutions A/S"},
    {"file": "contract_07_nonfinancial.md", "key": "contract_07_nonfinancial",
     "classification": "non_critical", "supplier": "DataBridge Solutions Ltd.",
     "customer": "ScandHR Analytics A/S"},
]


def _run_set(contracts: list[dict], expected: dict, analyser: ContractAnalyser, label: str) -> dict:
    """Run a list of contract specs through the heuristic engine and validate."""
    validation_results = []
    aggregate = {
        "total_gaps": 0,
        "total_na": 0,
        "total_matched": 0,
        "total_na_matched": 0,
        "total_false_positives": 0,
    }
    print(f"\n{'═'*60}")
    print(f"{label}")
    print(f"{'═'*60}")
    for spec in contracts:
        contract_path = CONTRACTS_DIR / spec["file"]
        metadata = ContractMetadata(
            contract_id=spec["key"],
            supplier_name=spec["supplier"],
            customer_name=spec["customer"],
            classification=spec["classification"],
            file_name=spec["file"],
        )
        contract_text = contract_path.read_text(encoding="utf-8")
        result = analyser.analyse_file(
            contract_path,
            classification=spec["classification"],
            contract_metadata=metadata,
        )
        # Pass 2: deterministic evidence-context validation (no API key required)
        result, _validation_decisions = run_evidence_validation_overlay(
            result, contract_text, budget=None, use_llm=False,
        )

        out_path = OUTPUT_DIR / f"{spec['key']}_result.json"
        out_path.write_text(result.to_json(), encoding="utf-8")

        val = validate_against_expected(result, expected, spec["key"])
        val["supplier"] = spec["supplier"]
        val["classification"] = spec["classification"]
        val["summary_stats"] = result.summary_stats
        val["validation_decisions"] = [
            {"req": d.requirement_id, "rule": d.rule_applied, "revised": d.revised_status.value}
            for d in _validation_decisions if d.revised_status != d.original_status
        ]
        validation_results.append(val)

        aggregate["total_gaps"] += val["expected_genuine_gaps"]
        aggregate["total_na"] += val["na_expectations"]
        aggregate["total_matched"] += val["matched"]
        aggregate["total_na_matched"] += val["na_matched"]
        aggregate["total_false_positives"] += len(val["false_positives"])

        icon = "✓" if val["accuracy_pct"] >= 80 else ("⚠" if val["accuracy_pct"] >= 50 else "✗")
        print(
            f"{icon} {spec['key']:38s}  recall={val['accuracy_pct']:5.1f}%"
            f"  matched={val['matched']}/{val['expected_genuine_gaps']}"
            f"  na={val['na_matched']}/{val['na_expectations']}"
            f"  fp={len(val['false_positives'])}"
        )
        for m in val.get("missed", []):
            print(f"    MISSED  {m['id']:15s}  expected={m['expected']:10s}  actual={m['actual']}")
        for m in val.get("na_missed", []):
            print(f"    NA-MISS {m['id']:15s}  expected=na          actual={m['actual']}")
        for fp in val.get("false_positives", []):
            print(f"    FP      {fp['id']:15s}  actual={fp['actual_status']}")

    tp = aggregate["total_matched"]
    fp = aggregate["total_false_positives"]
    fn = aggregate["total_gaps"] - tp
    precision = round(tp / (tp + fp) * 100, 1) if (tp + fp) > 0 else 0.0
    recall = round(tp / (tp + fn) * 100, 1) if (tp + fn) > 0 else 100.0
    f1 = round(2 * precision * recall / (precision + recall), 1) if (precision + recall) > 0 else 0.0

    aggregate["tp"] = tp
    aggregate["fp"] = fp
    aggregate["fn"] = fn
    aggregate["precision_pct"] = precision
    aggregate["recall_pct"] = recall
    aggregate["f1_pct"] = f1

    print(f"\n{'─'*60}")
    print(f"Genuine gaps:  TP={tp}  FP={fp}  FN={fn}")
    print(f"N/A scope:     matched={aggregate['total_na_matched']}/{aggregate['total_na']}")
    print(f"Precision: {precision}%   Recall: {recall}%   F1: {f1}%")

    return {"aggregate": aggregate, "results": validation_results}


def _run_llm_comparison(
    contracts: list[dict],
    expected: dict,
    analyser: ContractAnalyser,
    budget: BudgetGuard,
    label: str,
    heuristic_aggregate: dict,
) -> dict:
    """
    Re-run contracts through heuristic, apply LLM overlay, validate, and
    print a side-by-side heuristic vs LLM-enhanced comparison.

    Returns the LLM-enhanced aggregate dict.
    """
    print(f"\n{'═'*60}")
    print(f"{label} — LLM OVERLAY")
    print(f"{'═'*60}")
    print(f"Budget: {budget.remaining_dkk():.2f} DKK remaining of {budget.limit_dkk:.0f} DKK limit")

    llm_aggregate = {
        "total_gaps": 0,
        "total_na": 0,
        "total_matched": 0,
        "total_na_matched": 0,
        "total_false_positives": 0,
    }
    total_decisions = 0
    total_upgrades = 0

    for spec in contracts:
        contract_path = CONTRACTS_DIR / spec["file"]
        contract_text = contract_path.read_text(encoding="utf-8")
        metadata = ContractMetadata(
            contract_id=spec["key"],
            supplier_name=spec["supplier"],
            customer_name=spec["customer"],
            classification=spec["classification"],
            file_name=spec["file"],
        )
        result = analyser.analyse_file(
            contract_path,
            classification=spec["classification"],
            contract_metadata=metadata,
        )
        try:
            result, decisions = run_overlay(result, contract_text, budget)
        except BudgetExceededError as e:
            print(f"  ⚠ Budget exceeded during overlay — {spec['key']} kept heuristic status")
            print(f"    {e}")
            decisions = []
        except EnvironmentError as e:
            print(f"  ✗ {e}")
            return {}

        # Pass 2: validate EVIDENCE_FOUND context (with optional LLM deepening)
        try:
            result, val_decisions = run_evidence_validation_overlay(result, contract_text, budget)
        except BudgetExceededError:
            val_decisions = []

        all_decisions = decisions + val_decisions
        upgrades = [d for d in all_decisions if d.revised_status != d.original_status]
        total_decisions += len(all_decisions)
        total_upgrades += len(upgrades)

        val = validate_against_expected(result, expected, spec["key"])
        llm_aggregate["total_gaps"] += val["expected_genuine_gaps"]
        llm_aggregate["total_na"] += val["na_expectations"]
        llm_aggregate["total_matched"] += val["matched"]
        llm_aggregate["total_na_matched"] += val["na_matched"]
        llm_aggregate["total_false_positives"] += len(val["false_positives"])

        icon = "✓" if val["accuracy_pct"] >= 80 else ("⚠" if val["accuracy_pct"] >= 50 else "✗")
        print(
            f"{icon} {spec['key']:38s}  recall={val['accuracy_pct']:5.1f}%"
            f"  matched={val['matched']}/{val['expected_genuine_gaps']}"
            f"  fp={len(val['false_positives'])}"
            f"  upgrades={len(upgrades)}/{len(decisions)}"
        )
        for d in upgrades:
            conf = f"  conf={d.confidence:.2f}" if hasattr(d, "confidence") else f"  rule={d.rule_applied}"
            print(f"    CHANGE  {d.requirement_id:12s}  {d.original_status.value} → {d.revised_status.value}{conf}")

    tp = llm_aggregate["total_matched"]
    fp = llm_aggregate["total_false_positives"]
    fn = llm_aggregate["total_gaps"] - tp
    precision = round(tp / (tp + fp) * 100, 1) if (tp + fp) > 0 else 0.0
    recall = round(tp / (tp + fn) * 100, 1) if (tp + fn) > 0 else 100.0
    f1 = round(2 * precision * recall / (precision + recall), 1) if (precision + recall) > 0 else 0.0

    llm_aggregate["tp"] = tp
    llm_aggregate["fp"] = fp
    llm_aggregate["fn"] = fn
    llm_aggregate["precision_pct"] = precision
    llm_aggregate["recall_pct"] = recall
    llm_aggregate["f1_pct"] = f1
    llm_aggregate["total_decisions"] = total_decisions
    llm_aggregate["total_upgrades"] = total_upgrades

    h = heuristic_aggregate
    print(f"\n{'─'*60}")
    print(f"LLM overlay decisions: {total_decisions} reviewed, {total_upgrades} upgraded")
    print(f"Budget used: {budget.total_spent_dkk():.4f} DKK")
    print(f"\n{'─'*60}")
    print(f"{'':35s}  {'Heuristic':>12s}  {'LLM-enhanced':>12s}")
    print(f"{'─'*60}")
    print(f"{'Precision':35s}  {h.get('precision_pct', 0):>11.1f}%  {precision:>11.1f}%")
    print(f"{'Recall':35s}  {h.get('recall_pct', 0):>11.1f}%  {recall:>11.1f}%")
    print(f"{'F1':35s}  {h.get('f1_pct', 0):>11.1f}%  {f1:>11.1f}%")
    print(f"{'False positives':35s}  {h.get('fp', 0):>12d}  {fp:>12d}")
    print(f"{'True positives':35s}  {h.get('tp', 0):>12d}  {tp:>12d}")

    return llm_aggregate


def run_benchmark(holdout_only: bool = False, llm: bool = False) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    analyser = ContractAnalyser()

    report: dict = {
        "run_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "engine_version": "gamma7-engine-v2",
        "mode": "heuristic+llm" if llm else "heuristic",
    }

    if not holdout_only:
        with open(INSAMPLE_EXPECTED_FILE, encoding="utf-8") as f:
            insample_expected = yaml.safe_load(f)
        in_sample = _run_set(IN_SAMPLE_CONTRACTS, insample_expected, analyser, "IN-SAMPLE (5 contracts — tuning set)")
        in_sample["caveat"] = (
            "These 5 contracts were used to tune match_terms. "
            "Precision/recall reflects in-sample fit only — not generalisation."
        )
        print("\n⚠  IN-SAMPLE CAVEAT: Figures above are from the tuning set. See holdout results for generalisation estimate.")
        report["in_sample"] = in_sample

    with open(HOLDOUT_EXPECTED_FILE, encoding="utf-8") as f:
        holdout_expected = yaml.safe_load(f)
    holdout = _run_set(HOLDOUT_CONTRACTS, holdout_expected, analyser, "HOLDOUT (2 contracts — not seen during tuning)")
    holdout["caveat"] = (
        "These 2 contracts were NOT used to tune match_terms. "
        "Figures are an independent estimate of heuristic generalisation performance."
    )
    print("\n✓  HOLDOUT: Figures above are the independently verified generalisation estimate.")
    report["holdout"] = holdout

    if llm:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            print("\n" + "═" * 60)
            print("LLM OVERLAY — ANTHROPIC_API_KEY not set")
            print("═" * 60)
            print("Add to harvey/.env:  ANTHROPIC_API_KEY=sk-ant-...")
            print("Then rerun:          python gamma7/engine/run_benchmark.py --llm")
        else:
            budget = BudgetGuard()
            if not holdout_only:
                in_sample_llm = _run_llm_comparison(
                    IN_SAMPLE_CONTRACTS, insample_expected, analyser, budget,
                    "IN-SAMPLE (5 contracts)", in_sample["aggregate"],
                )
                if in_sample_llm:
                    report["in_sample_llm"] = in_sample_llm
            holdout_llm = _run_llm_comparison(
                HOLDOUT_CONTRACTS, holdout_expected, analyser, budget,
                "HOLDOUT (2 contracts)", holdout["aggregate"],
            )
            if holdout_llm:
                report["holdout_llm"] = holdout_llm
            print(f"\n💰 Total API spend this run: {budget.total_spent_dkk():.4f} DKK")

    report_path = OUTPUT_DIR / "benchmark_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nFull report written to: {report_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Gamma7 precision/recall benchmark")
    parser.add_argument("--holdout-only", action="store_true",
                        help="Run holdout contracts only (skip in-sample set)")
    parser.add_argument("--llm", action="store_true",
                        help="Enable LLM overlay comparison (requires ANTHROPIC_API_KEY)")
    args = parser.parse_args()
    run_benchmark(holdout_only=args.holdout_only, llm=args.llm)


if __name__ == "__main__":
    main()
