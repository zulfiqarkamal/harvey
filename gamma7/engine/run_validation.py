"""
Gamma7 — Batch validation runner.
Runs all 5 synthetic contracts through the gap analysis engine and validates
results against expected_findings.yaml.

Usage (from gamma7/engine/):
    python run_validation.py
"""

from __future__ import annotations

import json
import pathlib
import sys

import yaml

REPO_ROOT = pathlib.Path(__file__).parent.parent
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from analyser import ContractAnalyser, ContractMetadata, validate_against_expected

CONTRACTS_DIR = REPO_ROOT / "synthetic_contracts"
EXPECTED_FILE = CONTRACTS_DIR / "expected_findings.yaml"
OUTPUT_DIR = REPO_ROOT / "engine" / "output"

CONTRACTS = [
    {"file": "contract_01_cloudhost.md", "key": "contract_01_cloudhost", "classification": "critical_important", "supplier": "NordCloud Hosting A/S", "customer": "Nordbank Sparekasse"},
    {"file": "contract_02_payment.md", "key": "contract_02_payment", "classification": "critical_important", "supplier": "PayLink Technologies Ltd", "customer": "Fjord Finans A/S"},
    {"file": "contract_03_legacy.md", "key": "contract_03_legacy", "classification": "non_critical", "supplier": "DataCenter DK ApS", "customer": "Kapital Forsikring A/S"},
    {"file": "contract_04_saas.md", "key": "contract_04_saas", "classification": "non_critical", "supplier": "ComplianceTools International BV", "customer": "Coastal Payment Institution A/S"},
    {"file": "contract_05_good.md", "key": "contract_05_good", "classification": "critical_important", "supplier": "SecureVault Nordic A/S", "customer": "Nordisk Bank & Invest A/S"},
]


def run_all() -> dict:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(EXPECTED_FILE, encoding="utf-8") as f:
        expected = yaml.safe_load(f)
    analyser = ContractAnalyser()
    validation_results = []
    aggregate = {"total_gaps": 0, "total_matched": 0, "total_false_positives": 0}
    for spec in CONTRACTS:
        contract_path = CONTRACTS_DIR / spec["file"]
        metadata = ContractMetadata(
            contract_id=spec["key"],
            supplier_name=spec["supplier"],
            customer_name=spec["customer"],
            classification=spec["classification"],
            file_name=spec["file"],
        )
        result = analyser.analyse_file(contract_path, classification=spec["classification"], contract_metadata=metadata)
        out_path = OUTPUT_DIR / f"{spec['key']}_result.json"
        out_path.write_text(result.to_json(), encoding="utf-8")
        val = validate_against_expected(result, expected, spec["key"])
        val["supplier"] = spec["supplier"]
        val["classification"] = spec["classification"]
        val["summary_stats"] = result.summary_stats
        validation_results.append(val)
        aggregate["total_gaps"] += val["expected_gaps"]
        aggregate["total_matched"] += val["matched"]
        aggregate["total_false_positives"] += len(val["false_positives"])
        icon = "✓" if val["accuracy_pct"] >= 80 else "⚠" if val["accuracy_pct"] >= 50 else "✗"
        print(f"{icon} {spec['key']:30s}  accuracy={val['accuracy_pct']:5.1f}%  matched={val['matched']}/{val['expected_gaps']}  fp={len(val['false_positives'])}")
        for m in val.get("missed", []):
            print(f"    MISSED  {m['id']:15s}  expected={m['expected']:10s}  actual={m['actual']}")
    tp = aggregate["total_matched"]
    fp = aggregate["total_false_positives"]
    fn = aggregate["total_gaps"] - tp
    precision = round(tp / (tp + fp) * 100, 1) if (tp + fp) > 0 else 0.0
    recall = round(tp / (tp + fn) * 100, 1) if (tp + fn) > 0 else 100.0
    f1 = round(2 * precision * recall / (precision + recall), 1) if (precision + recall) > 0 else 0.0
    aggregate["overall_accuracy_pct"] = recall
    aggregate["tp"] = tp
    aggregate["fp"] = fp
    aggregate["fn"] = fn
    aggregate["precision_pct"] = precision
    aggregate["recall_pct"] = recall
    aggregate["f1_pct"] = f1
    import datetime
    report = {"run_date": datetime.datetime.now(datetime.timezone.utc).isoformat(), "engine_version": "gamma7-engine-v2", "contracts_tested": len(CONTRACTS), "aggregate": aggregate, "results": validation_results}
    report_path = OUTPUT_DIR / "validation_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\n{'─'*60}")
    print(f"TP={tp}  FP={fp}  FN={fn}")
    print(f"Precision: {precision}%   Recall: {recall}%   F1: {f1}%")
    print(f"\nReport written to: {report_path}")
    return report


if __name__ == "__main__":
    run_all()
