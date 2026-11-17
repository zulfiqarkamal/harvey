"""
Gamma7 — Report Generator
Converts a ContractAnalysisResult into a human-readable Markdown report.
Used by both Supplier Assurance (Agent 2) and Audit Intelligence (Agent 4).
"""

from __future__ import annotations

import datetime
import pathlib
from typing import Optional

from .analyser import ContractAnalysisResult, ComplianceStatus, RiskLevel

_STATUS_EMOJI = {
    ComplianceStatus.EVIDENCE_FOUND: "🔵",
    ComplianceStatus.MISSING: "🔴",
    ComplianceStatus.AMBIGUOUS: "🟡",
    ComplianceStatus.NOT_APPLICABLE: "⚪",
    ComplianceStatus.NOT_ASSESSED: "⬜",
    ComplianceStatus.SATISFIED: "🟢",
}

_RISK_LABEL = {
    RiskLevel.CRITICAL: "CRITICAL",
    RiskLevel.HIGH: "HIGH",
    RiskLevel.MEDIUM: "MEDIUM",
    RiskLevel.LOW: "LOW",
    RiskLevel.INFO: "INFO",
}


def generate_markdown(result: ContractAnalysisResult, include_evidence: bool = True) -> str:
    lines: list[str] = []

    _header(lines, result)
    _summary_table(lines, result)
    _findings_by_status(lines, result, "🔴 Gaps — Missing Requirements", ComplianceStatus.MISSING, include_evidence)
    _findings_by_status(lines, result, "🟡 Ambiguous — Requires Legal Review", ComplianceStatus.AMBIGUOUS, include_evidence)
    _findings_by_status(lines, result, "🔵 Evidence Found — Human Review Required", ComplianceStatus.EVIDENCE_FOUND, include_evidence)
    _remediation_section(lines, result)
    _footer(lines, result)

    return "\n".join(lines)


def _header(lines: list[str], result: ContractAnalysisResult) -> None:
    meta = result.contract
    overall_emoji = _STATUS_EMOJI.get(result.overall_status, "⬜")
    lines.extend([
        f"# Gamma7 Compliance Analysis Report",
        f"",
        f"**Analysis ID:** `{result.analysis_id}`  ",
        f"**Framework:** {result.framework}  ",
        f"**Contract:** {meta.supplier_name} — {getattr(meta, 'customer_name', 'N/A')}  ",
        f"**Classification:** {meta.classification}  ",
        f"**Generated:** {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"**Overall Status:** {overall_emoji} `{result.overall_status.value.upper()}`",
        f"",
        f"> ⚠️ **This report is a decision-support tool.** Findings require human legal review before any contractual or regulatory action. This is not legal advice.",
        f"",
        f"---",
        f"",
    ])


def _summary_table(lines: list[str], result: ContractAnalysisResult) -> None:
    stats = result.summary_stats
    lines.extend([
        f"## Summary",
        f"",
        f"| Status | Count |",
        f"|--------|-------|",
        f"| 🔴 Missing (gaps) | {stats.get('missing', 0)} |",
        f"| 🟡 Ambiguous | {stats.get('ambiguous', 0)} |",
        f"| 🔵 Evidence found (review needed) | {stats.get('evidence_found', 0)} |",
        f"| 🟢 Satisfied | {stats.get('satisfied', 0)} |",
        f"| ⚪ Not applicable | {stats.get('not_applicable', 0)} |",
        f"| ⬜ Not assessed | {stats.get('not_assessed', 0)} |",
        f"| **Total** | **{stats.get('total_requirements', 0)}** |",
        f"",
    ])
    high_gaps = stats.get('high_gaps', 0)
    critical_gaps = stats.get('critical_gaps', 0)
    if high_gaps or critical_gaps:
        lines.extend([
            f"⚡ **{critical_gaps} critical + {high_gaps} high-risk gaps** require immediate attention.",
            f"",
        ])


def _findings_by_status(
    lines: list[str],
    result: ContractAnalysisResult,
    heading: str,
    status: ComplianceStatus,
    include_evidence: bool,
) -> None:
    items = [a for a in result.assessments if a.status == status]
    if not items:
        return
    lines.extend([f"## {heading}", f""])
    for a in items:
        risk_badge = ""
        if a.risk_level:
            risk_badge = f" `{_RISK_LABEL.get(a.risk_level, a.risk_level)}`"
        lines.extend([
            f"### {a.requirement_id}{risk_badge}",
            f"**Article:** {a.requirement_article}  ",
            f"**Requirement:** {a.requirement_summary}",
            f"",
        ])
        if a.gap_description:
            lines.extend([f"**Finding:** {a.gap_description}", f""])
        if getattr(a, 'source_location', None):
            lines.extend([f"**Source location:** {a.source_location}", f""])
        if include_evidence and a.evidence:
            lines.append("**Contract passages:**")
            for ev in a.evidence[:2]:
                loc_parts = []
                if ev.get('section'):
                    loc_parts.append(f"§ {ev['section']}")
                if ev.get('paragraph_num'):
                    loc_parts.append(f"¶{ev['paragraph_num']}")
                loc_label = f" *({', '.join(loc_parts)})*" if loc_parts else ""
                lines.extend([
                    f"> {ev['text'][:300].strip()}{loc_label}",
                    f"",
                ])
        if a.remediation_suggestion:
            lines.extend([f"**Recommended action:** {a.remediation_suggestion}", f""])


def _remediation_section(lines: list[str], result: ContractAnalysisResult) -> None:
    tasks = result.remediation_tasks
    if not tasks:
        return
    lines.extend([
        f"## Remediation Tasks ({len(tasks)})",
        f"",
        f"| Priority | Requirement | Action |",
        f"|----------|-------------|--------|",
    ])
    priority_order = {"immediate": 0, "short_term": 1, "medium_term": 2}
    sorted_tasks = sorted(tasks, key=lambda t: priority_order.get(t.priority if hasattr(t, 'priority') else t.get('priority', 'medium_term'), 2))
    for t in sorted_tasks:
        req_id = t.requirement_id if hasattr(t, 'requirement_id') else t.get('requirement_id', '')
        priority = t.priority if hasattr(t, 'priority') else t.get('priority', '')
        action = t.action if hasattr(t, 'action') else t.get('action', '')
        lines.append(f"| {priority} | `{req_id}` | {action[:100]} |")
    lines.append("")


def _footer(lines: list[str], result: ContractAnalysisResult) -> None:
    lines.extend([
        f"---",
        f"",
        f"*Generated by Gamma7 Evidence & Compliance Engine — heuristic analysis. All findings require human legal review.*  ",
        f"*Engine: `{result.assessments[0].assessed_by if result.assessments else 'gamma7-engine-v1'}`*",
    ])


def save(result: ContractAnalysisResult, output_dir: str | pathlib.Path, stem: Optional[str] = None) -> dict[str, pathlib.Path]:
    """Save analysis result as JSON + Markdown. Returns paths dict."""
    out_dir = pathlib.Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = stem or result.analysis_id
    json_path = out_dir / f"{base}.json"
    md_path = out_dir / f"{base}.md"
    json_path.write_text(result.to_json(), encoding="utf-8")
    md_path.write_text(generate_markdown(result), encoding="utf-8")
    return {"json": json_path, "markdown": md_path}
