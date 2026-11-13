"""
CLI entrypoint for Gamma7 Audit Intelligence Agent.

Usage:
    python -m gamma7.audit_intelligence plan --org "Nordbank A/S" --scope iso27001 --output output/
    python -m gamma7.audit_intelligence plan --org "Fjord Finans" --scope iso27001 --exclude A.7.1,A.7.8 --cycle-years 3 --output output/

    python -m gamma7.audit_intelligence workpapers --org "Nordbank A/S" --scope iso27001 --output output/
    python -m gamma7.audit_intelligence workpapers --org "Fjord Finans" --scope iso27001 --exclude A.7.1,A.7.8 --output output/
    python -m gamma7.audit_intelligence workpapers --org "Name" --scope iso27001 --print-summary

    python -m gamma7.audit_intelligence report --org "Nordbank A/S" --scope iso27001 --output output/
    python -m gamma7.audit_intelligence report --org "Name" --scope iso27001 --auditor "Zulfiqar Kamal" --output output/
"""

from __future__ import annotations

import argparse
import pathlib
import sys

from .audit_universe import build_universe, build_program, save as save_outputs


def cmd_plan(args: argparse.Namespace) -> None:
    exclude = [x.strip() for x in (args.exclude or "").split(",") if x.strip()]
    scope = [s.strip() for s in args.scope.split(",") if s.strip()]

    universe = build_universe(
        organisation=args.org,
        scope=scope,
        exclude_control_ids=exclude or None,
    )
    program = build_program(universe, cycle_years=args.cycle_years)

    if args.output:
        paths = save_outputs(universe, program, args.output)
        m = universe.metadata
        print(f"Audit Universe built: {universe.universe_id}")
        print(f"Organisation: {args.org}")
        print(f"Scope: {', '.join(scope)}")
        print(f"Controls in scope: {m['in_scope']} ({m['critical']} critical, {m['high']} high, {m['medium']} medium)")
        print(f"Residual risk: {m['residual_high']} high, {m['residual_medium']} medium, {m['residual_low']} low")
        print(f"\nOutput:")
        for key, path in paths.items():
            print(f"  {key:20s}: {path}")
    else:
        from .audit_universe import generate_markdown
        print(generate_markdown(universe, program))


def cmd_workpapers(args: argparse.Namespace) -> None:
    from .workpaper import build_workpapers, generate_summary_report, save_workpapers

    exclude = [x.strip() for x in (args.exclude or "").split(",") if x.strip()]
    scope = [s.strip() for s in args.scope.split(",") if s.strip()]

    universe = build_universe(
        organisation=args.org,
        scope=scope,
        exclude_control_ids=exclude or None,
    )
    wps = build_workpapers(universe)

    if args.print_summary:
        print(generate_summary_report(wps))
        return

    if args.output:
        output_dir = pathlib.Path(args.output)
        paths = save_workpapers(wps, output_dir, include_individual=not args.no_individual)
        m = wps.engagement_metadata
        print(f"Working papers generated: {wps.engagement_id}")
        print(f"Organisation: {args.org}")
        print(f"Scope: {', '.join(scope)}")
        print(f"Work papers: {m['total_workpapers']} ({wps.tested_count} tested, {wps.exception_count} with exceptions)")
        print(f"\nOutput:")
        for key, path in paths.items():
            print(f"  {key:30s}: {path}")
    else:
        print(generate_summary_report(wps))


def cmd_report(args: argparse.Namespace) -> None:
    from .workpaper import build_workpapers
    from .audit_report import generate_audit_report, save_audit_report

    exclude = [x.strip() for x in (args.exclude or "").split(",") if x.strip()]
    scope = [s.strip() for s in args.scope.split(",") if s.strip()]

    universe = build_universe(
        organisation=args.org,
        scope=scope,
        exclude_control_ids=exclude or None,
    )
    wps = build_workpapers(universe)

    if args.output:
        path = save_audit_report(
            wps,
            pathlib.Path(args.output),
            auditor_name=args.auditor,
            engagement_period=args.period or None,
        )
        print(f"Audit report saved: {path}")
    else:
        print(generate_audit_report(
            wps,
            auditor_name=args.auditor,
            engagement_period=args.period or None,
        ))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m gamma7.audit_intelligence",
        description="Gamma7 Audit Intelligence Agent — audit universe, risk-scored program, and working papers",
    )
    sub = p.add_subparsers(dest="command", required=True)

    # --- plan subcommand ---
    plan = sub.add_parser("plan", help="Generate audit universe and program")
    plan.add_argument("--org", required=True, metavar="NAME", help="Organisation name")
    plan.add_argument("--scope", default="iso27001", metavar="SCOPES",
                      help="Comma-separated scope keys (default: iso27001)")
    plan.add_argument("--exclude", default="", metavar="CONTROL_IDS",
                      help="Comma-separated control IDs to exclude (e.g. A.7.1,A.7.8)")
    plan.add_argument("--cycle-years", type=int, default=3, metavar="N",
                      help="Number of years in the rolling audit cycle (default: 3)")
    plan.add_argument("--output", default=None, metavar="DIR",
                      help="Output directory for YAML + Markdown (prints to stdout if omitted)")

    # --- workpapers subcommand ---
    wp = sub.add_parser("workpapers", help="Generate blank IIA-standard working papers for all in-scope controls")
    wp.add_argument("--org", required=True, metavar="NAME", help="Organisation name")
    wp.add_argument("--scope", default="iso27001", metavar="SCOPES",
                    help="Comma-separated scope keys (default: iso27001)")
    wp.add_argument("--exclude", default="", metavar="CONTROL_IDS",
                    help="Comma-separated control IDs to exclude (e.g. A.7.1,A.7.8)")
    wp.add_argument("--output", default=None, metavar="DIR",
                    help="Output directory for JSON + YAML + Markdown (prints summary to stdout if omitted)")
    wp.add_argument("--no-individual", action="store_true",
                    help="Skip individual per-control Markdown files (only write summary and JSON/YAML)")
    wp.add_argument("--print-summary", action="store_true",
                    help="Print management summary report to stdout and exit")

    # --- report subcommand ---
    rp = sub.add_parser("report", help="Generate formal audit report (Markdown)")
    rp.add_argument("--org", required=True, metavar="NAME", help="Organisation name")
    rp.add_argument("--scope", default="iso27001", metavar="SCOPES",
                    help="Comma-separated scope keys (default: iso27001)")
    rp.add_argument("--exclude", default="", metavar="CONTROL_IDS",
                    help="Comma-separated control IDs to exclude")
    rp.add_argument("--auditor", default="Gamma7 Audit Intelligence", metavar="NAME",
                    help="Auditor name for the report header")
    rp.add_argument("--period", default="", metavar="TEXT",
                    help="Engagement period description (e.g. 'Q4 2026')")
    rp.add_argument("--output", default=None, metavar="DIR",
                    help="Output directory for Markdown report (prints to stdout if omitted)")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "plan":
        cmd_plan(args)
    elif args.command == "workpapers":
        cmd_workpapers(args)
    elif args.command == "report":
        cmd_report(args)


if __name__ == "__main__":
    main()
