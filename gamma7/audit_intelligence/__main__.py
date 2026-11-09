"""
CLI entrypoint for Gamma7 Audit Intelligence Agent.

Usage:
    python -m gamma7.audit_intelligence plan --org "Nordbank A/S" --scope iso27001 --output output/
    python -m gamma7.audit_intelligence plan --org "Fjord Finans" --scope iso27001 --exclude A.7.1,A.7.8 --quarters 4 --output output/
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
    program = build_program(universe, quarters=args.quarters)

    if args.output:
        paths = save_outputs(universe, program, args.output)
        m = universe.metadata
        print(f"Audit Universe built: {universe.universe_id}")
        print(f"Organisation: {args.org}")
        print(f"Scope: {', '.join(scope)}")
        print(f"Controls in scope: {m['in_scope']} ({m['critical']} critical, {m['high']} high, {m['medium']} medium)")
        print(f"\nOutput:")
        for key, path in paths.items():
            print(f"  {key:20s}: {path}")
    else:
        from .audit_universe import generate_markdown
        print(generate_markdown(universe, program))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m gamma7.audit_intelligence",
        description="Gamma7 Audit Intelligence Agent — audit universe and risk-scored audit program",
    )
    sub = p.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="Generate audit universe and program")
    plan.add_argument("--org", required=True, metavar="NAME", help="Organisation name")
    plan.add_argument("--scope", default="iso27001", metavar="SCOPES",
                      help="Comma-separated scope keys (default: iso27001)")
    plan.add_argument("--exclude", default="", metavar="CONTROL_IDS",
                      help="Comma-separated control IDs to exclude (e.g. A.7.1,A.7.8)")
    plan.add_argument("--quarters", type=int, default=4, metavar="N",
                      help="Number of audit quarters to plan (default: 4)")
    plan.add_argument("--output", default=None, metavar="DIR",
                      help="Output directory for YAML + Markdown (prints to stdout if omitted)")

    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "plan":
        cmd_plan(args)


if __name__ == "__main__":
    main()
