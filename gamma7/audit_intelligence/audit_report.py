"""
Gamma7 Audit Intelligence — Formal Audit Report Generator
==========================================================
Generates a formal management-level audit report from a completed WorkPaperSet.

Output structure follows standard IT internal audit report conventions:
  1. Report header (engagement metadata, auditor declaration)
  2. Executive Summary (scope, objective, overall conclusion, key messages)
  3. Methodology (approach, standards reference, testing coverage)
  4. Control Testing Results (per-control status table)
  5. Findings Detail (IIA 5-C per finding, severity-ordered)
  6. Remediation Action Plan (owner, deadline, priority per finding)
  7. Appendix A — Audit Universe (all in-scope controls)
  8. Appendix B — Testing Statistics

This module produces Markdown only. Word/PDF export is out of scope here.

No LLM calls. No side effects. Pure function over WorkPaperSet.
"""

from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone
from typing import Optional

from .workpaper import (
    WorkPaperSet,
    WorkPaper,
    Finding,
    FindingSeverity,
    FindingStatus,
    TestResult,
)

SEVERITY_EMOJI = {
    FindingSeverity.CRITICAL: "🔴",
    FindingSeverity.HIGH: "🟠",
    FindingSeverity.MEDIUM: "🟡",
    FindingSeverity.LOW: "🟢",
    FindingSeverity.INFO: "⬜",
}

STATUS_EMOJI = {
    TestResult.PASSED: "✅",
    TestResult.EXCEPTION: "⚠️",
    TestResult.NOT_TESTED: "⬜",
    TestResult.NOT_APPLICABLE: "➖",
}

OVERALL_CONCLUSIONS = {
    "clean": (
        "No significant control deficiencies were identified. "
        "Controls are designed and operating effectively for the period under review."
    ),
    "qualified": (
        "One or more control deficiencies were identified. "
        "The findings documented in this report require management attention and remediation "
        "within the agreed timeframes."
    ),
    "adverse": (
        "Significant control deficiencies were identified across multiple areas. "
        "The findings documented in this report represent material weaknesses that require "
        "immediate management attention and urgent remediation."
    ),
}


def _overall_conclusion(wps: WorkPaperSet) -> str:
    findings = wps.all_findings
    open_findings = [f for f in findings if f.status == FindingStatus.OPEN]
    critical = sum(1 for f in open_findings if f.severity == FindingSeverity.CRITICAL)
    high = sum(1 for f in open_findings if f.severity == FindingSeverity.HIGH)

    if not open_findings:
        return OVERALL_CONCLUSIONS["clean"]
    if critical > 0 or high >= 3:
        return OVERALL_CONCLUSIONS["adverse"]
    return OVERALL_CONCLUSIONS["qualified"]


def _tested_ratio(wps: WorkPaperSet) -> str:
    total = len(wps.work_papers)
    tested = wps.tested_count
    not_applicable = sum(1 for wp in wps.work_papers if wp.test_result == TestResult.NOT_APPLICABLE)
    effective = total - not_applicable
    if effective == 0:
        return "0 / 0"
    return f"{tested} / {effective}"


def generate_audit_report(
    wps: WorkPaperSet,
    auditor_name: str = "Gamma7 Audit Intelligence",
    report_date: Optional[str] = None,
    engagement_period: Optional[str] = None,
    standards_reference: str = "ISO 27001:2022 Annex A",
    confidentiality_notice: Optional[str] = None,
) -> str:
    """
    Generate a formal Markdown audit report from a completed WorkPaperSet.

    Args:
        wps: Completed (or partially completed) WorkPaperSet.
        auditor_name: Name of the responsible auditor or audit function.
        report_date: Date string for the report header (ISO 8601). Defaults to today UTC.
        engagement_period: Text description of the period under review.
        standards_reference: Audit standards used (default: ISO 27001:2022 Annex A).
        confidentiality_notice: Optional confidentiality disclaimer for the header.

    Returns:
        Formatted Markdown string.
    """
    now = datetime.now(timezone.utc)
    date_str = report_date or now.strftime("%d %B %Y")
    period_str = engagement_period or f"As at {now.strftime('%d %B %Y')}"
    conf = confidentiality_notice or (
        "This report is intended solely for the use of management. "
        "It should not be distributed to third parties without prior authorisation."
    )

    findings_all = wps.all_findings
    open_findings = [f for f in findings_all if f.status == FindingStatus.OPEN]
    fbs = wps.findings_by_severity
    conclusion = _overall_conclusion(wps)

    lines: list[str] = []

    # --- Header ---
    lines += [
        "---",
        f"**CONFIDENTIAL — INTERNAL AUDIT REPORT**  ",
        f"**Organisation:** {wps.organisation}  ",
        f"**Engagement ID:** `{wps.engagement_id}`  ",
        f"**Report Date:** {date_str}  ",
        f"**Period Under Review:** {period_str}  ",
        f"**Prepared by:** {auditor_name}  ",
        f"**Standards Reference:** {standards_reference}  ",
        "---",
        "",
        f"*{conf}*",
        "",
    ]

    # --- 1. Executive Summary ---
    lines += [
        "## 1. Executive Summary",
        "",
        f"This report presents the results of the IT internal audit engagement for **{wps.organisation}** "
        f"covering {len(wps.work_papers)} controls in scope under {standards_reference}.",
        "",
        "### Overall Conclusion",
        "",
        conclusion,
        "",
        "### Key Statistics",
        "",
        f"| | |",
        f"|---|---|",
        f"| Controls in scope | {len(wps.work_papers)} |",
        f"| Controls tested | {wps.tested_count} |",
        f"| Controls passed | {wps.passed_count} |",
        f"| Controls with exceptions | {wps.exception_count} |",
        f"| Total findings | {len(findings_all)} |",
        f"| Open findings | {len(open_findings)} |",
        "",
    ]

    if findings_all:
        lines += [
            "### Findings by Severity",
            "",
            "| Severity | Total | Open |",
            "|---|---|---|",
        ]
        for sev in FindingSeverity:
            bucket = fbs.get(sev.value, [])
            open_count = sum(1 for f in bucket if f.status == FindingStatus.OPEN)
            if bucket:
                emoji = SEVERITY_EMOJI[sev]
                lines.append(f"| {emoji} {sev.value} | {len(bucket)} | {open_count} |")
        lines.append("")

    # --- 2. Scope and Objectives ---
    lines += [
        "## 2. Scope and Objectives",
        "",
        f"The audit covered the following scope: **{', '.join(wps.scope)}**.",
        "",
        f"The objective was to independently assess whether controls specified under {standards_reference} "
        "are designed appropriately and operating effectively to manage identified risks.",
        "",
        f"**In-scope controls:** {len(wps.work_papers)}",
        "",
    ]

    # --- 3. Methodology ---
    lines += [
        "## 3. Methodology",
        "",
        "The audit was conducted in accordance with the International Standards for the Professional "
        "Practice of Internal Auditing (IPPF) issued by the Institute of Internal Auditors (IIA). "
        "Testing followed a risk-based approach, with controls rated Critical or High risk subject to "
        "deeper evidence review.",
        "",
        "**Testing approach:**",
        "",
        "- Inquiry and observation",
        "- Inspection of documentary evidence",
        "- Walkthrough testing of control procedures",
        "- Configuration and technical parameter review (where applicable)",
        "",
        f"**Testing coverage:** {_tested_ratio(wps)} effective controls tested.",
        "",
        "**Finding classification:**",
        "",
        "| Severity | Definition |",
        "|---|---|",
        "| 🔴 CRITICAL | Systemic failure; imminent breach or regulatory sanction risk |",
        "| 🟠 HIGH | Significant gap; likely exploitation or audit qualification without remediation |",
        "| 🟡 MEDIUM | Partial failure; mitigating controls present; manageable risk |",
        "| 🟢 LOW | Minor deviation; negligible residual risk |",
        "| ⬜ INFO | Best-practice observation; not a deficiency |",
        "",
    ]

    # --- 4. Control Testing Results ---
    lines += [
        "## 4. Control Testing Results",
        "",
        "| Control ID | Title | Domain | Inherent Risk | Result | Findings |",
        "|---|---|---|---|---|---|",
    ]
    for wp in sorted(wps.work_papers, key=lambda w: w.control_id):
        result_icon = STATUS_EMOJI.get(wp.test_result, "⬜")
        finding_count = len(wp.findings)
        open_count = len(wp.open_findings)
        finding_cell = ""
        if finding_count > 0:
            finding_cell = f"{finding_count} finding(s) ({open_count} open)"
        lines.append(
            f"| {wp.control_id} | {wp.control_title} | {wp.control_domain} "
            f"| {wp.inherent_risk} | {result_icon} {wp.test_result} | {finding_cell} |"
        )
    lines.append("")

    # --- 5. Findings Detail ---
    if findings_all:
        lines += [
            "## 5. Findings Detail",
            "",
        ]
        # Sort: open first, then by severity (CRITICAL → INFO), then by finding_id
        severity_order = {s: i for i, s in enumerate(FindingSeverity)}
        sorted_findings: list[tuple[WorkPaper, Finding]] = []
        for wp in wps.work_papers:
            for f in wp.findings:
                sorted_findings.append((wp, f))
        sorted_findings.sort(key=lambda x: (
            0 if x[1].status == FindingStatus.OPEN else 1,
            severity_order.get(x[1].severity, 99),
            x[1].finding_id,
        ))

        for wp, f in sorted_findings:
            sev_emoji = SEVERITY_EMOJI.get(f.severity, "")
            status_badge = f"**{f.status.value}**"
            lines += [
                f"### {f.finding_id}: {f.title}",
                "",
                f"| | |",
                f"|---|---|",
                f"| **Control** | {wp.control_id} — {wp.control_title} |",
                f"| **Severity** | {sev_emoji} {f.severity.value} |",
                f"| **Status** | {status_badge} |",
            ]
            if f.owner:
                lines.append(f"| **Owner** | {f.owner} |")
            if f.target_remediation_date:
                lines.append(f"| **Target Remediation** | {f.target_remediation_date} |")
            lines += [
                "",
                f"**Condition (What is):** {f.condition}",
                "",
                f"**Criteria (What should be):** {f.criteria}",
                "",
                f"**Cause (Root cause):** {f.cause}",
                "",
                f"**Effect (Risk/Impact):** {f.effect}",
                "",
                f"**Recommendation:** {f.recommendation}",
                "",
            ]
    else:
        lines += [
            "## 5. Findings Detail",
            "",
            "*No findings were identified during this engagement.*",
            "",
        ]

    # --- 6. Remediation Action Plan ---
    remediation_findings = [
        (wp, f)
        for wp in wps.work_papers
        for f in wp.open_findings
        if f.severity in (FindingSeverity.CRITICAL, FindingSeverity.HIGH, FindingSeverity.MEDIUM)
    ]
    lines += [
        "## 6. Remediation Action Plan",
        "",
    ]
    if remediation_findings:
        lines += [
            "| Finding ID | Title | Severity | Owner | Target Date | Status |",
            "|---|---|---|---|---|---|",
        ]
        for wp, f in remediation_findings:
            emoji = SEVERITY_EMOJI.get(f.severity, "")
            owner = f.owner or "To be assigned"
            target = f.target_remediation_date or "To be agreed"
            lines.append(
                f"| {f.finding_id} | {f.title} | {emoji} {f.severity.value} "
                f"| {owner} | {target} | {f.status.value} |"
            )
        lines.append("")
        lines += [
            "> **Note:** Management is requested to confirm ownership and target remediation dates "
            "for all open findings within 10 business days of this report.",
            "",
        ]
    else:
        lines += [
            "*No remediation actions required at this time.*",
            "",
        ]

    # --- Appendix A — Audit Universe ---
    lines += [
        "---",
        "",
        "## Appendix A — Audit Universe",
        "",
        "| Control ID | Title | Domain | Inherent Risk | Audit Objective |",
        "|---|---|---|---|---|",
    ]
    for wp in sorted(wps.work_papers, key=lambda w: w.control_id):
        obj = (wp.audit_objective or "")[:80].replace("|", "\\|")
        lines.append(
            f"| {wp.control_id} | {wp.control_title} | {wp.control_domain} "
            f"| {wp.inherent_risk} | {obj}… |"
        )
    lines.append("")

    # --- Appendix B — Testing Statistics ---
    result_counts: dict[str, int] = {}
    for wp in wps.work_papers:
        key = wp.test_result.value
        result_counts[key] = result_counts.get(key, 0) + 1

    lines += [
        "## Appendix B — Testing Statistics",
        "",
        "| Test Result | Count |",
        "|---|---|",
    ]
    for result, count in sorted(result_counts.items()):
        lines.append(f"| {result} | {count} |")
    lines += [
        "",
        f"| **Total controls** | **{len(wps.work_papers)}** |",
        "",
        f"*Report generated: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC*",
    ]

    return "\n".join(lines)


def save_audit_report(
    wps: WorkPaperSet,
    output_dir: pathlib.Path,
    *,
    auditor_name: str = "Gamma7 Audit Intelligence",
    report_date: Optional[str] = None,
    engagement_period: Optional[str] = None,
    standards_reference: str = "ISO 27001:2022 Annex A",
    confidentiality_notice: Optional[str] = None,
) -> pathlib.Path:
    """
    Generate and save the formal audit report as a Markdown file.

    Returns:
        Path to the saved report file.
    """
    output_dir = pathlib.Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    md = generate_audit_report(
        wps,
        auditor_name=auditor_name,
        report_date=report_date,
        engagement_period=engagement_period,
        standards_reference=standards_reference,
        confidentiality_notice=confidentiality_notice,
    )

    filename = f"{wps.engagement_id}_audit_report.md"
    path = output_dir / filename
    path.write_text(md, encoding="utf-8")
    return path
