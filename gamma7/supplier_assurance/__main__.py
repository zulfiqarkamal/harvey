"""
CLI entrypoint for Gamma7 Supplier Assurance Agent.

Usage:
    python -m gamma7.supplier_assurance analyze --contract path/to/contract.md
    python -m gamma7.supplier_assurance analyze --contract contract.pdf --classification critical_important --supplier "Acme A/S" --customer "Nordbank" --output output/
    python -m gamma7.supplier_assurance batch --config batch.yaml --output output/
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .pipeline import SupplierAssurancePipeline


def cmd_analyze(args: argparse.Namespace) -> None:
    pipeline = SupplierAssurancePipeline()
    result = pipeline.run(
        contract_path=args.contract,
        supplier_name=args.supplier,
        customer_name=args.customer,
        classification=args.classification,
        output_dir=args.output,
        contract_id=args.id,
    )

    if args.output:
        print(f"Analysis complete: {result.analysis_id}")
        print(f"Overall status:    {result.overall_status.upper()}")
        s = result.summary
        print(f"Missing:  {s.get('missing', 0)}")
        print(f"Ambiguous:{s.get('ambiguous', 0)}")
        print(f"Evidence: {s.get('evidence_found', 0)}")
        if result.output_paths:
            print(f"\nOutput:")
            for fmt, path in result.output_paths.items():
                print(f"  {fmt:8s}: {path}")
        for w in result.warnings:
            print(f"⚠ WARNING: {w}", file=sys.stderr)
    else:
        # Print Markdown to stdout when no output dir specified
        print(result.markdown_report)
        for w in result.warnings:
            print(f"⚠ WARNING: {w}", file=sys.stderr)


def cmd_batch(args: argparse.Namespace) -> None:
    import yaml
    with open(args.config, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    contracts = config.get("contracts", [])
    if not contracts:
        print("No contracts defined in config file.", file=sys.stderr)
        sys.exit(1)
    pipeline = SupplierAssurancePipeline()
    results = pipeline.run_batch(contracts, output_dir=args.output)
    summary = {
        "total": len(results),
        "by_status": {},
    }
    for r in results:
        summary["by_status"].setdefault(r.overall_status, 0)
        summary["by_status"][r.overall_status] += 1
    for r in results:
        icon = "🔴" if r.overall_status == "missing" else "🟡" if r.overall_status == "ambiguous" else "🔵"
        print(f"{icon} {r.supplier_name:40s} {r.overall_status.upper()}")
    print(f"\n{len(results)} contracts analysed → {args.output}")
    if args.json_summary:
        print(json.dumps(summary, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m gamma7.supplier_assurance",
        description="Gamma7 Supplier Assurance Agent — DORA Art. 30 + GDPR Art. 28",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # --- analyze ---
    a = sub.add_parser("analyze", help="Analyse a single contract")
    a.add_argument("--contract", required=True, metavar="PATH", help="Contract file (.md, .txt, .pdf)")
    a.add_argument("--classification", choices=["critical_important", "non_critical"],
                   default="non_critical",
                   help="DORA function classification (default: non_critical)")
    a.add_argument("--supplier", default="Unknown Supplier", metavar="NAME")
    a.add_argument("--customer", default="Unknown Customer", metavar="NAME")
    a.add_argument("--id", default=None, metavar="CONTRACT_ID", help="Optional contract reference ID")
    a.add_argument("--output", default=None, metavar="DIR",
                   help="Output directory for JSON + Markdown report (prints to stdout if omitted)")

    # --- batch ---
    b = sub.add_parser("batch", help="Analyse multiple contracts from a YAML config")
    b.add_argument("--config", required=True, metavar="YAML", help="Batch config file")
    b.add_argument("--output", required=True, metavar="DIR", help="Output directory")
    b.add_argument("--json-summary", action="store_true", help="Print JSON summary to stdout")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "analyze":
        cmd_analyze(args)
    elif args.command == "batch":
        cmd_batch(args)


if __name__ == "__main__":
    main()
