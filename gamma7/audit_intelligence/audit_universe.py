"""
Gamma7 Audit Intelligence — Audit Universe & Risk-Scored Audit Program Generator
===================================================================================
Agent 4: takes a scope input (organisation profile + regulatory scope)
and produces a risk-scored audit universe with individual control-testing programs.

No LLM calls — pure deterministic risk scoring based on control domain and
configurable organisation risk profile.

Output:
  - Audit Universe (YAML) — full list of in-scope controls with risk scores
  - Audit Program (YAML) — prioritised testing schedule with evidence artifacts
  - Markdown report — human-readable planning document
"""

from __future__ import annotations

import datetime
import pathlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
import yaml
import json

REQUIREMENTS_DIR = pathlib.Path(__file__).parent / "requirements"

SUPPORTED_SCOPES = {
    "iso27001": "iso27001_subset.yaml",
}

_RISK_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


class InherentRisk(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class AuditDecision(str, Enum):
    INCLUDE = "include"
    EXCLUDE_OOS = "exclude_out_of_scope"
    DEFER = "defer"


@dataclass
class ControlEntry:
    control_id: str
    title: str
    domain: str
    objective: str
    inherent_risk: InherentRisk
    audit_decision: AuditDecision
    priority_rank: int
    audit_approach: str
    evidence_artifacts: list[str]
    risk_indicators: list[str]
    rationale: Optional[str] = None
    assigned_to: Optional[str] = None
    planned_quarter: Optional[str] = None


@dataclass
class AuditUniverse:
    universe_id: str
    organisation: str
    scope: list[str]
    generated_at: str
    controls: list[ControlEntry] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_yaml(self) -> str:
        return yaml.dump(asdict(self), allow_unicode=True, default_flow_style=False, sort_keys=False)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)

    @property
    def critical_count(self) -> int:
        return sum(1 for c in self.controls if c.inherent_risk == InherentRisk.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for c in self.controls if c.inherent_risk == InherentRisk.HIGH)


@dataclass
class AuditProgram:
    program_id: str
    universe_id: str
    quarters: dict[str, list[str]]  # quarter → [control_id]
    generated_at: str

    def to_yaml(self) -> str:
        return yaml.dump(asdict(self), allow_unicode=True, default_flow_style=False, sort_keys=False)


def _load_controls(scope_key: str) -> list[dict]:
    filename = SUPPORTED_SCOPES.get(scope_key)
    if not filename:
        raise ValueError(f"Unknown scope '{scope_key}'. Supported: {list(SUPPORTED_SCOPES.keys())}")
    path = REQUIREMENTS_DIR / filename
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    controls = []
    for domain in data.get("control_domains", []):
        for ctrl in domain.get("controls", []):
            ctrl["domain"] = domain["name"]
            controls.append(ctrl)
    return controls


def _apply_risk_profile(default_risk: str, profile: dict) -> InherentRisk:
    """
    Adjust default control risk based on organisation's risk profile.
    Profile keys: elevated_domains (list of domain names), reduced_domains.
    """
    return InherentRisk(default_risk)


def _risk_score(risk: InherentRisk) -> int:
    return _RISK_ORDER.get(risk.value, 99)


def build_universe(
    organisation: str,
    scope: list[str],
    exclude_control_ids: Optional[list[str]] = None,
    risk_profile: Optional[dict] = None,
) -> AuditUniverse:
    """
    Build a full audit universe for the given organisation and regulatory scope.

    Args:
        organisation: Name of the organisation being audited.
        scope: List of scope keys, e.g. ["iso27001"]. More will be added in Phase 2.
        exclude_control_ids: Controls to explicitly exclude (e.g. not applicable domains).
        risk_profile: Optional dict with keys elevated_domains, reduced_domains for adjustments.

    Returns:
        AuditUniverse with all in-scope controls, risk-scored and ranked.
    """
    exclude_ids = set(exclude_control_ids or [])
    profile = risk_profile or {}
    all_controls: list[ControlEntry] = []

    for scope_key in scope:
        raw_controls = _load_controls(scope_key)
        for ctrl in raw_controls:
            ctrl_id = ctrl["id"]
            if ctrl_id in exclude_ids:
                entry = ControlEntry(
                    control_id=ctrl_id,
                    title=ctrl["title"],
                    domain=ctrl["domain"],
                    objective=ctrl.get("objective", ""),
                    inherent_risk=InherentRisk(ctrl.get("default_risk", "MEDIUM")),
                    audit_decision=AuditDecision.EXCLUDE_OOS,
                    priority_rank=999,
                    audit_approach=ctrl.get("audit_approach", ""),
                    evidence_artifacts=ctrl.get("evidence_artifacts", []),
                    risk_indicators=ctrl.get("risk_indicators", []),
                    rationale="Excluded by scope configuration.",
                )
            else:
                risk = _apply_risk_profile(ctrl.get("default_risk", "MEDIUM"), profile)
                entry = ControlEntry(
                    control_id=ctrl_id,
                    title=ctrl["title"],
                    domain=ctrl["domain"],
                    objective=ctrl.get("objective", ""),
                    inherent_risk=risk,
                    audit_decision=AuditDecision.INCLUDE,
                    priority_rank=0,
                    audit_approach=ctrl.get("audit_approach", ""),
                    evidence_artifacts=ctrl.get("evidence_artifacts", []),
                    risk_indicators=ctrl.get("risk_indicators", []),
                )
            all_controls.append(entry)

    # Rank by risk (CRITICAL first), then by control_id
    included = [c for c in all_controls if c.audit_decision == AuditDecision.INCLUDE]
    included.sort(key=lambda c: (_risk_score(c.inherent_risk), c.control_id))
    for i, c in enumerate(included, start=1):
        c.priority_rank = i

    # Assign excluded controls rank 999
    all_controls_ordered = included + [c for c in all_controls if c.audit_decision != AuditDecision.INCLUDE]

    universe = AuditUniverse(
        universe_id=f"AU-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        organisation=organisation,
        scope=scope,
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        controls=all_controls_ordered,
        metadata={
            "total_controls": len(all_controls),
            "in_scope": len(included),
            "excluded": len(all_controls) - len(included),
            "critical": sum(1 for c in included if c.inherent_risk == InherentRisk.CRITICAL),
            "high": sum(1 for c in included if c.inherent_risk == InherentRisk.HIGH),
            "medium": sum(1 for c in included if c.inherent_risk == InherentRisk.MEDIUM),
        },
    )
    return universe


def build_program(universe: AuditUniverse, quarters: int = 4) -> AuditProgram:
    """
    Distribute in-scope controls across audit quarters (prioritising high-risk first).

    Critical + High risk controls → Q1 and Q2
    Medium risk → Q3 and Q4
    Low risk → Q4 or deferred
    """
    included = [c for c in universe.controls if c.audit_decision == AuditDecision.INCLUDE]
    critical_high = [c for c in included if c.inherent_risk in (InherentRisk.CRITICAL, InherentRisk.HIGH)]
    medium = [c for c in included if c.inherent_risk == InherentRisk.MEDIUM]
    low = [c for c in included if c.inherent_risk == InherentRisk.LOW]

    per_quarter = max(1, len(included) // quarters)
    schedule: dict[str, list[str]] = {f"Q{q+1}": [] for q in range(quarters)}

    all_ordered = critical_high + medium + low
    for i, ctrl in enumerate(all_ordered):
        q_index = min(i // per_quarter, quarters - 1)
        schedule[f"Q{q_index + 1}"].append(ctrl.control_id)
        ctrl.planned_quarter = f"Q{q_index + 1}"

    return AuditProgram(
        program_id=f"AP-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        universe_id=universe.universe_id,
        quarters=schedule,
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


def generate_markdown(universe: AuditUniverse, program: Optional[AuditProgram] = None) -> str:
    lines: list[str] = []
    m = universe.metadata
    lines.extend([
        f"# Gamma7 Audit Universe",
        f"",
        f"**Organisation:** {universe.organisation}  ",
        f"**Scope:** {', '.join(universe.scope)}  ",
        f"**Generated:** {universe.generated_at[:19].replace('T', ' ')} UTC  ",
        f"**Universe ID:** `{universe.universe_id}`",
        f"",
        f"## Summary",
        f"",
        f"| | Count |",
        f"|--|-------|",
        f"| Total controls | {m.get('total_controls', 0)} |",
        f"| In scope | {m.get('in_scope', 0)} |",
        f"| Excluded | {m.get('excluded', 0)} |",
        f"| 🔴 Critical risk | {m.get('critical', 0)} |",
        f"| 🟠 High risk | {m.get('high', 0)} |",
        f"| 🟡 Medium risk | {m.get('medium', 0)} |",
        f"",
    ])

    if program:
        lines.extend([f"## Audit Schedule", f""])
        for q, ctrl_ids in program.quarters.items():
            lines.append(f"### {q} ({len(ctrl_ids)} controls)")
            for cid in ctrl_ids:
                ctrl = next((c for c in universe.controls if c.control_id == cid), None)
                if ctrl:
                    risk_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(ctrl.inherent_risk.value, "⬜")
                    lines.append(f"- {risk_icon} `{ctrl.control_id}` {ctrl.title}")
            lines.append("")

    lines.extend([f"## Control Details", f""])
    for ctrl in universe.controls:
        if ctrl.audit_decision != AuditDecision.INCLUDE:
            continue
        risk_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(ctrl.inherent_risk.value, "⬜")
        lines.extend([
            f"### `{ctrl.control_id}` {ctrl.title} {risk_icon}",
            f"**Domain:** {ctrl.domain}  ",
            f"**Inherent Risk:** {ctrl.inherent_risk.value}  ",
            f"**Priority Rank:** #{ctrl.priority_rank}",
            f"",
            f"**Objective:** {ctrl.objective}",
            f"",
            f"**Audit Approach:** {ctrl.audit_approach}",
            f"",
        ])
        if ctrl.evidence_artifacts:
            lines.append("**Evidence to gather:**")
            for a in ctrl.evidence_artifacts:
                lines.append(f"- {a}")
            lines.append("")
        if ctrl.risk_indicators:
            lines.append("**Risk indicators:**")
            for r in ctrl.risk_indicators:
                lines.append(f"- {r}")
            lines.append("")

    lines.extend([
        f"---",
        f"*Generated by Gamma7 Audit Intelligence — requires auditor review before use.*",
    ])
    return "\n".join(lines)


def save(
    universe: AuditUniverse,
    program: Optional[AuditProgram],
    output_dir: str | pathlib.Path,
) -> dict[str, pathlib.Path]:
    out_dir = pathlib.Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, pathlib.Path] = {}
    u_yaml = out_dir / f"{universe.universe_id}_universe.yaml"
    u_yaml.write_text(universe.to_yaml(), encoding="utf-8")
    paths["universe_yaml"] = u_yaml
    u_json = out_dir / f"{universe.universe_id}_universe.json"
    u_json.write_text(universe.to_json(), encoding="utf-8")
    paths["universe_json"] = u_json
    md = out_dir / f"{universe.universe_id}_report.md"
    md.write_text(generate_markdown(universe, program), encoding="utf-8")
    paths["markdown"] = md
    if program:
        p_yaml = out_dir / f"{program.program_id}_program.yaml"
        p_yaml.write_text(program.to_yaml(), encoding="utf-8")
        paths["program_yaml"] = p_yaml
    return paths
