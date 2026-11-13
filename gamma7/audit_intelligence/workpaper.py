"""
Gamma7 Audit Intelligence — Working Papers & Findings
======================================================
Structured audit working papers per ISO 27001 control, following IIA standards
and ISAE 3402 / ISO 19011 audit documentation conventions.

Output per control:
  - WorkPaper: test steps performed, evidence gathered, test result, findings
  - Finding: Condition / Criteria / Cause / Effect / Recommendation (IIA format)
  - WorkPaperSet → management summary report

No LLM calls — pure structure. AI overlay sits above this module.
"""

from __future__ import annotations

import datetime
import pathlib
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
import yaml
import json

from .audit_universe import AuditUniverse, AuditDecision, InherentRisk, ControlEntry


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TestResult(str, Enum):
    PASSED = "PASSED"
    EXCEPTION = "EXCEPTION"
    NOT_TESTED = "NOT_TESTED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class FindingStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    ACCEPTED = "ACCEPTED"           # Risk accepted by management
    IN_REMEDIATION = "IN_REMEDIATION"


class FindingSeverity(str, Enum):
    """
    Aligned with IIA severity scale and ISO 27001 nonconformity classification.
    CRITICAL  — systemic failure, imminent risk of breach or regulatory sanction
    HIGH      — significant control weakness, likely exploitation if not remediated
    MEDIUM    — partial control failure, mitigating controls present
    LOW       — minor deviation, negligible residual risk
    INFO      — observation / best-practice recommendation, not a deficiency
    """
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


# ---------------------------------------------------------------------------
# Finding dataclass  (IIA 5-C format: Condition/Criteria/Cause/Effect/Recommendation)
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    finding_id: str
    control_id: str
    title: str

    # IIA five-element structure
    condition: str          # What IS: the factual situation observed
    criteria: str           # What SHOULD BE: the standard, policy or requirement
    cause: str              # WHY the gap exists (root cause)
    effect: str             # So WHAT: the risk or impact if not remediated
    recommendation: str     # HOW to fix it

    severity: FindingSeverity
    status: FindingStatus = FindingStatus.OPEN

    # Tracking
    management_response: Optional[str] = None
    target_remediation_date: Optional[str] = None
    owner: Optional[str] = None
    evidence_ref: Optional[str] = None     # Reference to supporting evidence item

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# WorkPaper dataclass — one per in-scope control
# ---------------------------------------------------------------------------

@dataclass
class WorkPaper:
    workpaper_id: str
    control_id: str
    control_title: str
    control_domain: str
    inherent_risk: str                          # InherentRisk value
    planned_quarter: Optional[str]

    # Audit execution fields
    audit_objective: str
    test_steps_performed: list[str] = field(default_factory=list)
    evidence_gathered: list[str] = field(default_factory=list)
    test_result: TestResult = TestResult.NOT_TESTED
    findings: list[Finding] = field(default_factory=list)

    # Auditor metadata
    auditor_notes: Optional[str] = None
    tested_by: Optional[str] = None
    tested_date: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_date: Optional[str] = None

    # Pre-populated from control definition (read-only reference)
    expected_evidence_artifacts: list[str] = field(default_factory=list)
    risk_indicators: list[str] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return len(self.findings) > 0

    @property
    def open_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.status == FindingStatus.OPEN]

    @property
    def critical_findings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == FindingSeverity.CRITICAL]

    def add_finding(
        self,
        title: str,
        condition: str,
        criteria: str,
        cause: str,
        effect: str,
        recommendation: str,
        severity: FindingSeverity,
        **kwargs,
    ) -> Finding:
        seq = len(self.findings) + 1
        finding = Finding(
            finding_id=f"{self.workpaper_id}-F{seq:02d}",
            control_id=self.control_id,
            title=title,
            condition=condition,
            criteria=criteria,
            cause=cause,
            effect=effect,
            recommendation=recommendation,
            severity=severity,
            **kwargs,
        )
        self.findings.append(finding)
        return finding

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# WorkPaperSet — the full audit engagement container
# ---------------------------------------------------------------------------

@dataclass
class WorkPaperSet:
    engagement_id: str
    universe_id: str
    organisation: str
    scope: list[str]
    generated_at: str
    work_papers: list[WorkPaper] = field(default_factory=list)
    engagement_metadata: dict = field(default_factory=dict)

    # ---------------------------------------------------------------------------
    # Computed properties
    # ---------------------------------------------------------------------------

    @property
    def tested_count(self) -> int:
        return sum(1 for wp in self.work_papers if wp.test_result != TestResult.NOT_TESTED)

    @property
    def passed_count(self) -> int:
        return sum(1 for wp in self.work_papers if wp.test_result == TestResult.PASSED)

    @property
    def exception_count(self) -> int:
        return sum(1 for wp in self.work_papers if wp.test_result == TestResult.EXCEPTION)

    @property
    def all_findings(self) -> list[Finding]:
        return [f for wp in self.work_papers for f in wp.findings]

    @property
    def open_findings(self) -> list[Finding]:
        return [f for wp in self.work_papers for f in wp.open_findings]

    @property
    def findings_by_severity(self) -> dict[str, list[Finding]]:
        result: dict[str, list[Finding]] = {s.value: [] for s in FindingSeverity}
        for f in self.all_findings:
            result[f.severity.value].append(f)
        return result

    def get_workpaper(self, control_id: str) -> Optional[WorkPaper]:
        return next((wp for wp in self.work_papers if wp.control_id == control_id), None)

    def to_yaml(self) -> str:
        return yaml.dump(asdict(self), allow_unicode=True, default_flow_style=False, sort_keys=False)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkPaperSet":
        work_papers = []
        for wp_data in data.get("work_papers", []):
            findings = []
            for f in wp_data.pop("findings", []):
                findings.append(Finding(
                    finding_id=f["finding_id"],
                    control_id=f["control_id"],
                    title=f["title"],
                    condition=f["condition"],
                    criteria=f["criteria"],
                    cause=f["cause"],
                    effect=f["effect"],
                    recommendation=f["recommendation"],
                    severity=FindingSeverity(f["severity"]),
                    status=FindingStatus(f.get("status", FindingStatus.OPEN.value)),
                    owner=f.get("owner"),
                    target_remediation_date=f.get("target_remediation_date"),
                    management_response=f.get("management_response"),
                    actual_remediation_date=f.get("actual_remediation_date"),
                ))
            wp = WorkPaper(
                workpaper_id=wp_data["workpaper_id"],
                control_id=wp_data["control_id"],
                control_title=wp_data["control_title"],
                control_domain=wp_data["control_domain"],
                inherent_risk=wp_data["inherent_risk"],
                planned_quarter=wp_data.get("planned_quarter"),
                audit_objective=wp_data["audit_objective"],
                test_steps_performed=wp_data.get("test_steps_performed", []),
                evidence_gathered=wp_data.get("evidence_gathered", []),
                test_result=TestResult(wp_data.get("test_result", TestResult.NOT_TESTED.value)),
                findings=findings,
                auditor_notes=wp_data.get("auditor_notes"),
                tested_by=wp_data.get("tested_by"),
                tested_date=wp_data.get("tested_date"),
                reviewed_by=wp_data.get("reviewed_by"),
                reviewed_date=wp_data.get("reviewed_date"),
                expected_evidence_artifacts=wp_data.get("expected_evidence_artifacts", []),
                risk_indicators=wp_data.get("risk_indicators", []),
            )
            work_papers.append(wp)
        return cls(
            engagement_id=data["engagement_id"],
            universe_id=data["universe_id"],
            organisation=data["organisation"],
            scope=data["scope"],
            generated_at=data["generated_at"],
            work_papers=work_papers,
            engagement_metadata=data.get("engagement_metadata", {}),
        )

    @classmethod
    def from_json(cls, text: str) -> "WorkPaperSet":
        return cls.from_dict(json.loads(text))


# ---------------------------------------------------------------------------
# Factory — build blank work papers from an AuditUniverse
# ---------------------------------------------------------------------------

def build_workpapers(universe: AuditUniverse) -> WorkPaperSet:
    """
    Create a blank WorkPaperSet from an AuditUniverse.
    One WorkPaper is created per in-scope control, pre-populated with
    the control's audit objective, expected evidence, and risk indicators.
    Test results and findings are left empty for auditor completion.
    """
    engagement_id = f"WP-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    work_papers: list[WorkPaper] = []

    for ctrl in universe.controls:
        if ctrl.audit_decision != AuditDecision.INCLUDE:
            continue

        wp = WorkPaper(
            workpaper_id=f"{engagement_id}-{ctrl.control_id.replace('.', '')}",
            control_id=ctrl.control_id,
            control_title=ctrl.title,
            control_domain=ctrl.domain,
            inherent_risk=ctrl.inherent_risk.value,
            planned_quarter=ctrl.planned_quarter,
            audit_objective=ctrl.objective,
            expected_evidence_artifacts=list(ctrl.evidence_artifacts),
            risk_indicators=list(ctrl.risk_indicators),
        )
        work_papers.append(wp)

    wps = WorkPaperSet(
        engagement_id=engagement_id,
        universe_id=universe.universe_id,
        organisation=universe.organisation,
        scope=universe.scope,
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        work_papers=work_papers,
        engagement_metadata={
            "total_workpapers": len(work_papers),
            "scope": universe.scope,
        },
    )
    return wps


# ---------------------------------------------------------------------------
# Markdown generators
# ---------------------------------------------------------------------------

_SEVERITY_ICON = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🟢",
    "INFO": "⬜",
}

_RESULT_ICON = {
    TestResult.PASSED: "✅",
    TestResult.EXCEPTION: "❌",
    TestResult.NOT_TESTED: "⬜",
    TestResult.NOT_APPLICABLE: "➖",
}


def generate_workpaper_markdown(wp: WorkPaper) -> str:
    """Format a single working paper as Markdown (IIA-style)."""
    lines: list[str] = []
    risk_icon = _SEVERITY_ICON.get(wp.inherent_risk, "⬜")
    result_icon = _RESULT_ICON.get(wp.test_result, "⬜")

    lines.extend([
        f"# Working Paper: `{wp.control_id}` {wp.control_title}",
        f"",
        f"| Field | Value |",
        f"|---|---|",
        f"| **Working Paper ID** | `{wp.workpaper_id}` |",
        f"| **Domain** | {wp.control_domain} |",
        f"| **Inherent Risk** | {risk_icon} {wp.inherent_risk} |",
        f"| **Planned Quarter** | {wp.planned_quarter or '—'} |",
        f"| **Test Result** | {result_icon} {wp.test_result.value} |",
        f"| **Tested by** | {wp.tested_by or '—'} |",
        f"| **Tested date** | {wp.tested_date or '—'} |",
        f"| **Reviewed by** | {wp.reviewed_by or '—'} |",
        f"",
        f"## Audit Objective",
        f"",
        f"{wp.audit_objective}",
        f"",
    ])

    if wp.expected_evidence_artifacts:
        lines.extend([f"## Expected Evidence", f""])
        for a in wp.expected_evidence_artifacts:
            lines.append(f"- {a}")
        lines.append("")

    if wp.risk_indicators:
        lines.extend([f"## Risk Indicators", f""])
        for r in wp.risk_indicators:
            lines.append(f"- {r}")
        lines.append("")

    if wp.test_steps_performed:
        lines.extend([f"## Test Steps Performed", f""])
        for i, step in enumerate(wp.test_steps_performed, 1):
            lines.append(f"{i}. {step}")
        lines.append("")

    if wp.evidence_gathered:
        lines.extend([f"## Evidence Gathered", f""])
        for e in wp.evidence_gathered:
            lines.append(f"- {e}")
        lines.append("")

    if wp.auditor_notes:
        lines.extend([f"## Auditor Notes", f"", wp.auditor_notes, ""])

    if wp.findings:
        lines.extend([f"## Findings ({len(wp.findings)})", f""])
        for f_obj in wp.findings:
            sev_icon = _SEVERITY_ICON.get(f_obj.severity.value, "⬜")
            lines.extend([
                f"### {sev_icon} `{f_obj.finding_id}` {f_obj.title}",
                f"",
                f"**Severity:** {f_obj.severity.value}  ",
                f"**Status:** {f_obj.status.value}",
                f"",
                f"**Condition (What is):**  ",
                f"{f_obj.condition}",
                f"",
                f"**Criteria (What should be):**  ",
                f"{f_obj.criteria}",
                f"",
                f"**Cause (Root cause):**  ",
                f"{f_obj.cause}",
                f"",
                f"**Effect (Risk/Impact):**  ",
                f"{f_obj.effect}",
                f"",
                f"**Recommendation:**  ",
                f"{f_obj.recommendation}",
                f"",
            ])
            if f_obj.management_response:
                lines.extend([
                    f"**Management Response:**  ",
                    f"{f_obj.management_response}",
                    f"",
                ])
            if f_obj.target_remediation_date:
                lines.append(f"**Target Remediation Date:** {f_obj.target_remediation_date}")
                lines.append("")

    lines.extend([
        "---",
        "*Working paper requires senior auditor sign-off before issue. "
        "Generated by Gamma7 Audit Intelligence.*",
    ])
    return "\n".join(lines)


def generate_summary_report(wps: WorkPaperSet) -> str:
    """
    Generate a management-level summary report from a WorkPaperSet.
    Format: executive summary → statistics → findings by severity → remediation tracker.
    """
    lines: list[str] = []
    fbs = wps.findings_by_severity
    total_findings = len(wps.all_findings)
    open_findings = len(wps.open_findings)

    lines.extend([
        f"# Gamma7 Audit — Management Summary Report",
        f"",
        f"**Organisation:** {wps.organisation}  ",
        f"**Scope:** {', '.join(wps.scope)}  ",
        f"**Engagement ID:** `{wps.engagement_id}`  ",
        f"**Generated:** {wps.generated_at[:19].replace('T', ' ')} UTC",
        f"",
        f"## Executive Summary",
        f"",
        f"This report summarises the results of the IT audit engagement covering "
        f"{len(wps.work_papers)} in-scope controls. A total of {total_findings} findings "
        f"were identified, of which {open_findings} remain open and require management action.",
        f"",
        f"## Testing Statistics",
        f"",
        f"| | Count |",
        f"|---|---|",
        f"| Total controls in scope | {len(wps.work_papers)} |",
        f"| Controls tested | {wps.tested_count} |",
        f"| Passed | {wps.passed_count} |",
        f"| Exception (findings) | {wps.exception_count} |",
        f"| Not yet tested | {len(wps.work_papers) - wps.tested_count} |",
        f"",
        f"## Findings Summary",
        f"",
        f"| Severity | Total | Open |",
        f"|---|---|---|",
    ])

    for sev in FindingSeverity:
        all_sev = fbs.get(sev.value, [])
        open_sev = [f for f in all_sev if f.status == FindingStatus.OPEN]
        icon = _SEVERITY_ICON.get(sev.value, "⬜")
        lines.append(f"| {icon} {sev.value} | {len(all_sev)} | {len(open_sev)} |")

    lines.append("")

    if wps.all_findings:
        lines.extend([f"## Findings Detail", f""])
        for sev in FindingSeverity:
            sev_findings = fbs.get(sev.value, [])
            if not sev_findings:
                continue
            icon = _SEVERITY_ICON.get(sev.value, "⬜")
            lines.extend([f"### {icon} {sev.value} Findings", f""])
            for f_obj in sev_findings:
                lines.extend([
                    f"#### `{f_obj.finding_id}` {f_obj.title}",
                    f"**Control:** `{f_obj.control_id}`  ",
                    f"**Status:** {f_obj.status.value}  ",
                    f"**Owner:** {f_obj.owner or '—'}  ",
                    f"**Target date:** {f_obj.target_remediation_date or '—'}",
                    f"",
                    f"**Condition:** {f_obj.condition}",
                    f"",
                    f"**Effect:** {f_obj.effect}",
                    f"",
                    f"**Recommendation:** {f_obj.recommendation}",
                    f"",
                ])
                if f_obj.management_response:
                    lines.extend([
                        f"**Management Response:** {f_obj.management_response}",
                        f"",
                    ])

    # Remediation tracker
    open_with_date = [f for f in wps.open_findings if f.target_remediation_date]
    open_without_date = [f for f in wps.open_findings if not f.target_remediation_date]

    lines.extend([
        f"## Remediation Tracker",
        f"",
        f"| Finding ID | Control | Severity | Owner | Target Date | Status |",
        f"|---|---|---|---|---|---|",
    ])
    for f_obj in sorted(wps.open_findings,
                        key=lambda x: list(FindingSeverity).index(FindingSeverity(x.severity.value))):
        icon = _SEVERITY_ICON.get(f_obj.severity.value, "⬜")
        lines.append(
            f"| `{f_obj.finding_id}` | `{f_obj.control_id}` | {icon} {f_obj.severity.value} "
            f"| {f_obj.owner or '—'} | {f_obj.target_remediation_date or 'TBD'} "
            f"| {f_obj.status.value} |"
        )

    lines.extend([
        f"",
        f"---",
        f"*Report generated by Gamma7 Audit Intelligence. Requires senior auditor review "
        f"and management sign-off before distribution. This is a draft working document — "
        f"not a final audit opinion.*",
    ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------

def save_workpapers(
    wps: WorkPaperSet,
    output_dir: str | pathlib.Path,
    include_individual: bool = True,
) -> dict[str, pathlib.Path]:
    """
    Save a WorkPaperSet to disk.

    Args:
        wps: The WorkPaperSet to save.
        output_dir: Directory to write files into.
        include_individual: If True, also write one .md per WorkPaper.

    Returns:
        Dict mapping key → path for every file written.
    """
    out_dir = pathlib.Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, pathlib.Path] = {}

    summary_md = out_dir / f"{wps.engagement_id}_summary.md"
    summary_md.write_text(generate_summary_report(wps), encoding="utf-8")
    paths["summary_md"] = summary_md

    full_json = out_dir / f"{wps.engagement_id}_workpapers.json"
    full_json.write_text(wps.to_json(), encoding="utf-8")
    paths["full_json"] = full_json

    full_yaml = out_dir / f"{wps.engagement_id}_workpapers.yaml"
    full_yaml.write_text(wps.to_yaml(), encoding="utf-8")
    paths["full_yaml"] = full_yaml

    if include_individual:
        wp_dir = out_dir / "workpapers"
        wp_dir.mkdir(exist_ok=True)
        for wp in wps.work_papers:
            safe_id = wp.control_id.replace(".", "_")
            wp_path = wp_dir / f"{safe_id}.md"
            wp_path.write_text(generate_workpaper_markdown(wp), encoding="utf-8")
            paths[f"wp_{safe_id}"] = wp_path

    return paths
