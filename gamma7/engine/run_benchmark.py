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
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from analyser import ContractAnalyser, ContractMetadata, validate_against_expected

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
        result = analyser.analyse_file(
            contract_path,
            classification=spec["classification"],
            contract_metadata=metadata,
        )
        out_path = OUTPUT_DIR / f"{spec['key']}_result.json"
        out_path.write_text(result.to_json(), encoding="utf-8")

        val = validate_against_expected(result, expected, spec["key"])
        val["supplier"] = spec["supplier"]
        val["classification"] = spec["classification"]
        val["summary_stats"] = result.summary_stats
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


def _llm_overlay_placeholder() -> None:
    print("\n" + "═" * 60)
    print("LLM OVERLAY COMPARISON (not yet implemented)")
    print("═" * 60)
    print("Requires: ANTHROPIC_API_KEY environment variable")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("Status: ANTHROPIC_API_KEY not set — heuristic-only run")
        return
    print(
        "Status: ANTHROPIC_API_KEY is set but LLM overlay scaffold is not yet wired up.\n"
        "To implement: add an LLM-enhanced analyser path in analyser.py that calls\n"
        "ContractAnalyser with use_llm=True, then compare gap sets here."
    )


def run_benchmark(holdout_only: bool = False, llm: bool = False) -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    analyser = ContractAnalyser()

    report: dict = {
        "run_date": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "engine_version": "gamma7-engine-v2",
        "mode": "heuristic",
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
        _llm_overlay_placeholder()

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
