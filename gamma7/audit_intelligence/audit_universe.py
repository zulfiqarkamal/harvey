"""
Gamma7 Audit Intelligence — Audit Universe & Risk-Scored Audit Program Generator
===================================================================================
Agent 4: takes a scope input (organisation profile + regulatory scope)
and produces a risk-scored audit universe with individual control-testing programs.

No LLM calls — pure deterministic risk scoring based on control domain and
configurable organisation risk profile.

Output:
  - Audit Universe (YAML) — full list of in-scope controls with risk scores
  - Audit Program (YAML) — prioritised 3-year rolling plan with evidence artifacts
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


class ControlRating(str, Enum):
    """Effectiveness of controls mitigating inherent risk (IIA / ISACA standard)."""
    WEAK = "WEAK"           # Controls absent, poorly designed, or consistently failing
    PARTIAL = "PARTIAL"     # Controls exist but have significant gaps or inconsistent operation
    EFFECTIVE = "EFFECTIVE" # Controls well-designed and consistently operating


class ResidualRisk(str, Enum):
    """Qualitative residual risk after considering control effectiveness."""
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ReviewType(str, Enum):
    FULL_AUDIT = "full_audit"
    KEY_CONTROL_REVIEW = "key_control_review"


class AuditDecision(str, Enum):
    INCLUDE = "include"
    EXCLUDE_OOS = "exclude_out_of_scope"
    DEFER = "defer"


# IIA-aligned residual risk matrix: (InherentRisk, ControlRating) → ResidualRisk
# Conservative: CRITICAL + PARTIAL stays HIGH; HIGH + EFFECTIVE drops to LOW
_RESIDUAL_MATRIX: dict[tuple[str, str], str] = {
    ("CRITICAL", "WEAK"):      "HIGH",
    ("CRITICAL", "PARTIAL"):   "HIGH",
    ("CRITICAL", "EFFECTIVE"): "MEDIUM",
    ("HIGH",     "WEAK"):      "HIGH",
    ("HIGH",     "PARTIAL"):   "MEDIUM",
    ("HIGH",     "EFFECTIVE"): "LOW",
    ("MEDIUM",   "WEAK"):      "MEDIUM",
    ("MEDIUM",   "PARTIAL"):   "LOW",
    ("MEDIUM",   "EFFECTIVE"): "LOW",
    ("LOW",      "WEAK"):      "LOW",
    ("LOW",      "PARTIAL"):   "LOW",
    ("LOW",      "EFFECTIVE"): "LOW",
}


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
    # Control effectiveness and residual risk
    control_rating: ControlRating = ControlRating.PARTIAL
    residual_risk: Optional[ResidualRisk] = None
    # Tracking fields (from document methodology)
    risk_owner: Optional[str] = None
    last_assessed_date: Optional[str] = None
    open_findings_count: int = 0
    # Planning
    rationale: Optional[str] = None
    assigned_to: Optional[str] = None
    planned_quarter: Optional[str] = None  # e.g. "Year 1", "Year 2", "Year 3"
    review_type: Optional[str] = None      # "full_audit" or "key_control_review"


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
    cycle_years: int  # 3-year rolling plan
    plan: dict[str, list[str]]        # "Year 1" / "Year 2" / "Year 3" → [control_id]
    plan_types: dict[str, str]        # control_id → "full_audit" or "key_control_review"
    generated_at: str

    @property
    def quarters(self) -> dict[str, list[str]]:
        """Backward-compatibility alias for plan."""
        return self.plan

    def to_yaml(self) -> str:
        return yaml.dump(
            {k: v for k, v in asdict(self).items() if k != "plan_types"},
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )


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


def _apply_risk_profile(default_risk: str, control_id: str, profile: dict) -> InherentRisk:
    """
    Adjust inherent risk based on organisation risk profile.

    Profile keys:
        elevated_domains: list of domain names → upgrade risk one level
        reduced_domains:  list of domain names → downgrade risk one level
        elevated_controls: list of specific control IDs → force HIGH
    """
    elevated_domains: list[str] = profile.get("elevated_domains", [])
    reduced_domains: list[str] = profile.get("reduced_domains", [])
    elevated_controls: list[str] = profile.get("elevated_controls", [])

    risk = InherentRisk(default_risk)

    if control_id in elevated_controls:
        return InherentRisk.HIGH

    # Domain-level elevation/reduction requires knowing the domain — callers pass it via profile
    domain = profile.get("_current_domain", "")
    _up = {"LOW": "MEDIUM", "MEDIUM": "HIGH", "HIGH": "CRITICAL", "CRITICAL": "CRITICAL"}
    _down = {"CRITICAL": "HIGH", "HIGH": "MEDIUM", "MEDIUM": "LOW", "LOW": "LOW"}

    if domain in elevated_domains:
        risk = InherentRisk(_up.get(risk.value, risk.value))
    elif domain in reduced_domains:
        risk = InherentRisk(_down.get(risk.value, risk.value))

    return risk


def _compute_residual_risk(inherent: InherentRisk, rating: ControlRating) -> ResidualRisk:
    """Look up residual risk from the IIA-aligned 3×3 matrix."""
    key = (inherent.value, rating.value)
    result = _RESIDUAL_MATRIX.get(key, "MEDIUM")
    return ResidualRisk(result)


def _risk_score(risk: InherentRisk) -> int:
    return _RISK_ORDER.get(risk.value, 99)


def build_universe(
    organisation: str,
    scope: list[str],
    exclude_control_ids: Optional[list[str]] = None,
    risk_profile: Optional[dict] = None,
    control_ratings: Optional[dict[str, str]] = None,
) -> AuditUniverse:
    """
    Build a full audit universe for the given organisation and regulatory scope.

    Args:
        organisation: Name of the organisation being audited.
        scope: List of scope keys, e.g. ["iso27001"].
        exclude_control_ids: Controls to explicitly exclude.
        risk_profile: Optional dict with keys elevated_domains, reduced_domains,
                      elevated_controls for inherent risk adjustments.
        control_ratings: Optional dict mapping control_id → "WEAK"/"PARTIAL"/"EFFECTIVE".
                         Defaults to PARTIAL for all controls if not supplied.

    Returns:
        AuditUniverse with all in-scope controls, risk-scored and ranked.
    """
    exclude_ids = set(exclude_control_ids or [])
    profile = risk_profile or {}
    ratings = control_ratings or {}
    all_controls: list[ControlEntry] = []

    for scope_key in scope:
        raw_controls = _load_controls(scope_key)
        for ctrl in raw_controls:
            ctrl_id = ctrl["id"]
            domain = ctrl["domain"]
            profile_with_domain = {**profile, "_current_domain": domain}

            if ctrl_id in exclude_ids:
                entry = ControlEntry(
                    control_id=ctrl_id,
                    title=ctrl["title"],
                    domain=domain,
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
                inherent = _apply_risk_profile(ctrl.get("default_risk", "MEDIUM"), ctrl_id, profile_with_domain)
                rating_str = ratings.get(ctrl_id, "PARTIAL")
                control_rating = ControlRating(rating_str)
                residual = _compute_residual_risk(inherent, control_rating)
                entry = ControlEntry(
                    control_id=ctrl_id,
                    title=ctrl["title"],
                    domain=domain,
                    objective=ctrl.get("objective", ""),
                    inherent_risk=inherent,
                    audit_decision=AuditDecision.INCLUDE,
                    priority_rank=0,
                    audit_approach=ctrl.get("audit_approach", ""),
                    evidence_artifacts=ctrl.get("evidence_artifacts", []),
                    risk_indicators=ctrl.get("risk_indicators", []),
                    control_rating=control_rating,
                    residual_risk=residual,
                )
            all_controls.append(entry)

    # Rank by residual risk (included), then inherent, then control_id
    included = [c for c in all_controls if c.audit_decision == AuditDecision.INCLUDE]
    _res_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    included.sort(key=lambda c: (
        _res_order.get(c.residual_risk.value if c.residual_risk else "MEDIUM", 1),
        _risk_score(c.inherent_risk),
        c.control_id,
    ))
    for i, c in enumerate(included, start=1):
        c.priority_rank = i

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
            "residual_high": sum(1 for c in included if c.residual_risk == ResidualRisk.HIGH),
            "residual_medium": sum(1 for c in included if c.residual_risk == ResidualRisk.MEDIUM),
            "residual_low": sum(1 for c in included if c.residual_risk == ResidualRisk.LOW),
        },
    )
    return universe


def build_program(universe: AuditUniverse, cycle_years: int = 3) -> AuditProgram:
    """
    Build a rolling 3-year risk-based audit plan (IIA / document methodology):

      - Residual HIGH  → full audit every year (Years 1, 2, 3)
      - Residual MEDIUM → full audit every 2 years (Years 1 and 3)
      - Residual LOW   → 33% key-control review annually (all 3 years, lighter scope)

    Args:
        universe: Completed AuditUniverse.
        cycle_years: Length of the rolling cycle. Default 3.

    Returns:
        AuditProgram with plan keyed by "Year 1" … "Year N".
    """
    included = [c for c in universe.controls if c.audit_decision == AuditDecision.INCLUDE]
    plan: dict[str, list[str]] = {f"Year {y}": [] for y in range(1, cycle_years + 1)}
    plan_types: dict[str, str] = {}

    for ctrl in included:
        residual = ctrl.residual_risk or ResidualRisk.MEDIUM

        if residual == ResidualRisk.HIGH:
            # Annual full audit — appears every year
            years = list(range(1, cycle_years + 1))
            rtype = ReviewType.FULL_AUDIT.value
        elif residual == ResidualRisk.MEDIUM:
            # Biennial full audit — Year 1, Year 3 (odd years)
            years = [y for y in range(1, cycle_years + 1) if y % 2 == 1]
            rtype = ReviewType.FULL_AUDIT.value
        else:
            # LOW — key-control review in Year 1 only (33% of cycle = Year 1 of 3)
            years = [1]
            rtype = ReviewType.KEY_CONTROL_REVIEW.value

        for y in years:
            key = f"Year {y}"
            if key in plan:
                plan[key].append(ctrl.control_id)

        plan_types[ctrl.control_id] = rtype
        # Set planned_quarter to first scheduled year for ControlEntry
        ctrl.planned_quarter = f"Year {years[0]}" if years else None
        ctrl.review_type = rtype

    return AuditProgram(
        program_id=f"AP-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')}",
        universe_id=universe.universe_id,
        cycle_years=cycle_years,
        plan=plan,
        plan_types=plan_types,
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
        f"| 🔴 Critical inherent risk | {m.get('critical', 0)} |",
        f"| 🟠 High inherent risk | {m.get('high', 0)} |",
        f"| 🟡 Medium inherent risk | {m.get('medium', 0)} |",
        f"| 🔴 High residual risk | {m.get('residual_high', 0)} |",
        f"| 🟡 Medium residual risk | {m.get('residual_medium', 0)} |",
        f"| 🟢 Low residual risk | {m.get('residual_low', 0)} |",
        f"",
    ])

    if program:
        lines.extend([f"## Audit Schedule ({program.cycle_years}-Year Rolling Plan)", f""])
        for year_key, ctrl_ids in sorted(program.plan.items()):
            lines.append(f"### {year_key} ({len(ctrl_ids)} engagements)")
            for cid in ctrl_ids:
                ctrl = next((c for c in universe.controls if c.control_id == cid), None)
                if ctrl:
                    res = ctrl.residual_risk.value if ctrl.residual_risk else "?"
                    res_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(res, "⬜")
                    rtype = program.plan_types.get(cid, "")
                    rtype_label = " *(key-control review)*" if rtype == "key_control_review" else ""
                    lines.append(f"- {res_icon} `{ctrl.control_id}` {ctrl.title}{rtype_label}")
            lines.append("")

    lines.extend([f"## Control Details", f""])
    for ctrl in universe.controls:
        if ctrl.audit_decision != AuditDecision.INCLUDE:
            continue
        inh_icon = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🟢"}.get(ctrl.inherent_risk.value, "⬜")
        res_icon = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}.get(
            ctrl.residual_risk.value if ctrl.residual_risk else "", "⬜"
        )
        lines.extend([
            f"### `{ctrl.control_id}` {ctrl.title}",
            f"**Domain:** {ctrl.domain}  ",
            f"**Inherent Risk:** {ctrl.inherent_risk.value} {inh_icon}  ",
            f"**Control Rating:** {ctrl.control_rating.value}  ",
            f"**Residual Risk:** {ctrl.residual_risk.value if ctrl.residual_risk else 'N/A'} {res_icon}  ",
            f"**Priority Rank:** #{ctrl.priority_rank}",
        ])
        if ctrl.risk_owner:
            lines.append(f"**Risk Owner:** {ctrl.risk_owner}  ")
        if ctrl.last_assessed_date:
            lines.append(f"**Last Assessed:** {ctrl.last_assessed_date}  ")
        if ctrl.open_findings_count:
            lines.append(f"**Open Findings:** {ctrl.open_findings_count}  ")
        lines.extend([
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


def update_universe_from_workpapers(
    universe: AuditUniverse,
    work_paper_set: "WorkPaperSet",  # forward reference — avoids circular import
    assessed_date: Optional[str] = None,
) -> AuditUniverse:
    """
    Close the feedback loop: apply completed audit findings back to the universe.

    For each WorkPaper in work_paper_set that has been tested (test_result ≠ NOT_TESTED):
      1. Set last_assessed_date on the matching ControlEntry.
      2. Count open findings → open_findings_count.
      3. Derive a new ControlRating from TestResult:
           PASSED                → EFFECTIVE
           EXCEPTION + no CRITICAL findings → PARTIAL
           EXCEPTION + any CRITICAL finding → WEAK
           NOT_TESTED / NOT_APPLICABLE    → keep existing rating
      4. Recompute residual_risk via _RESIDUAL_MATRIX.

    The universe is mutated in-place and returned.  Metadata residual_* counts
    are refreshed to reflect the new ratings.

    Args:
        universe:         AuditUniverse to update.
        work_paper_set:   Completed (or partially completed) WorkPaperSet.
        assessed_date:    ISO-format date string to stamp on updated entries.
                          Defaults to today (UTC).
    Returns:
        The same AuditUniverse, mutated.
    """
    from .workpaper import TestResult, FindingSeverity, FindingStatus  # local import

    today = assessed_date or datetime.datetime.now(datetime.timezone.utc).date().isoformat()

    ctrl_index: dict[str, ControlEntry] = {
        c.control_id: c for c in universe.controls
        if c.audit_decision == AuditDecision.INCLUDE
    }

    for wp in work_paper_set.work_papers:
        ctrl = ctrl_index.get(wp.control_id)
        if ctrl is None:
            continue

        # Only update controls that were actually tested
        if wp.test_result in (TestResult.NOT_TESTED, TestResult.NOT_APPLICABLE):
            continue

        ctrl.last_assessed_date = wp.tested_date or today

        open_findings = [
            f for f in wp.findings
            if f.status == FindingStatus.OPEN
        ]
        ctrl.open_findings_count = len(open_findings)

        # Map test result → control rating
        if wp.test_result == TestResult.PASSED:
            new_rating = ControlRating.EFFECTIVE
        else:  # EXCEPTION
            has_critical = any(
                f.severity == FindingSeverity.CRITICAL for f in open_findings
            )
            new_rating = ControlRating.WEAK if has_critical else ControlRating.PARTIAL

        ctrl.control_rating = new_rating
        ctrl.residual_risk = _compute_residual_risk(ctrl.inherent_risk, new_rating)

    # Refresh residual counts in metadata
    included = [c for c in universe.controls if c.audit_decision == AuditDecision.INCLUDE]
    universe.metadata["residual_high"] = sum(
        1 for c in included if c.residual_risk == ResidualRisk.HIGH
    )
    universe.metadata["residual_medium"] = sum(
        1 for c in included if c.residual_risk == ResidualRisk.MEDIUM
    )
    universe.metadata["residual_low"] = sum(
        1 for c in included if c.residual_risk == ResidualRisk.LOW
    )

    return universe


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
