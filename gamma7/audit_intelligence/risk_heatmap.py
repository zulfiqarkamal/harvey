"""
Audit Intelligence — 3×3 Risk Heatmap (Agent 4 only)

Computes inherent and residual risk scores for an audit engagement based on
WorkPaperSet findings and test results. Uses the ISACA/IIA/COSO 3×3 matrix
(Likelihood × Impact = 1–9).

This module belongs exclusively to Agent 4 (Audit Intelligence).
Agent 2 (Supplier Assurance) has no risk matrix.
"""

from __future__ import annotations

from .workpaper import FindingSeverity, TestResult, WorkPaperSet

# FindingSeverity → (likelihood, impact) for inherent risk
_SEVERITY_TO_COMPONENTS: dict[str, tuple[int, int]] = {
    FindingSeverity.CRITICAL.value: (3, 3),  # 9 — High
    FindingSeverity.HIGH.value:     (3, 2),  # 6 — High
    FindingSeverity.MEDIUM.value:   (2, 2),  # 4 — Medium
    FindingSeverity.LOW.value:      (1, 2),  # 2 — Low
    FindingSeverity.INFO.value:     (1, 1),  # 1 — Low
}

# Control risk derived from WorkPaper test_result
_TEST_RESULT_TO_CONTROL_RISK: dict[str, str] = {
    TestResult.PASSED.value:      "low",     # controls operating effectively
    TestResult.EXCEPTION.value:   "high",    # control failure detected
    TestResult.NOT_TESTED.value:  "medium",  # untested — unknown effectiveness
}

# Residual = inherent × (1 − reduction)
_CONTROL_REDUCTION: dict[str, float] = {
    "low":    0.65,
    "medium": 0.45,
    "high":   0.10,
}


def _score_to_rating(score: float) -> str:
    if score <= 2.0:
        return "low"
    if score <= 4.0:
        return "medium"
    return "high"


def compute_risk_heatmap(work_paper_set: WorkPaperSet) -> dict:
    """
    Compute a 3×3 risk heatmap for an audit engagement.

    Inherent risk is derived from the highest-severity finding on each
    work paper. Control risk is derived from the work paper's test_result.
    Work papers with no findings default to INFO-level inherent risk.

    Returns::

        {
          "engagement_id": "...",
          "organisation": "...",
          "inherent_risk_counts": {"low": n, "medium": n, "high": n},
          "residual_risk_counts": {"low": n, "medium": n, "high": n},
          "work_papers": [
            {
              "workpaper_id": "WP-001",
              "control_id": "...",
              "control_title": "...",
              "highest_finding_severity": "HIGH",
              "inherent_likelihood": 3,
              "inherent_impact": 2,
              "inherent_score": 6,
              "inherent_rating": "high",
              "control_risk": "high",
              "residual_score": 5.4,
              "residual_rating": "high"
            }, ...
          ],
          "advisory": "..."
        }
    """
    inherent_counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    residual_counts: dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    papers_out = []

    severity_order = [
        FindingSeverity.CRITICAL,
        FindingSeverity.HIGH,
        FindingSeverity.MEDIUM,
        FindingSeverity.LOW,
        FindingSeverity.INFO,
    ]

    for wp in work_paper_set.work_papers:
        # Determine highest-severity finding (or INFO when none)
        highest_sev = FindingSeverity.INFO
        for sev in severity_order:
            if any(f.severity == sev for f in wp.findings):
                highest_sev = sev
                break

        likelihood, impact = _SEVERITY_TO_COMPONENTS.get(
            highest_sev.value, (1, 1)
        )
        inherent_score = likelihood * impact
        inherent_rating = _score_to_rating(inherent_score)
        inherent_counts[inherent_rating] += 1

        ctrl_risk = _TEST_RESULT_TO_CONTROL_RISK.get(
            wp.test_result.value, "medium"
        )
        reduction = _CONTROL_REDUCTION[ctrl_risk]
        res_score = round(inherent_score * (1.0 - reduction), 2)
        res_rating = _score_to_rating(res_score)
        residual_counts[res_rating] += 1

        papers_out.append({
            "workpaper_id": wp.workpaper_id,
            "control_id": wp.control_id,
            "control_title": wp.control_title,
            "highest_finding_severity": highest_sev.value,
            "inherent_likelihood": likelihood,
            "inherent_impact": impact,
            "inherent_score": inherent_score,
            "inherent_rating": inherent_rating,
            "control_risk": ctrl_risk,
            "residual_score": res_score,
            "residual_rating": res_rating,
        })

    return {
        "engagement_id": work_paper_set.engagement_id,
        "organisation": work_paper_set.organisation,
        "inherent_risk_counts": inherent_counts,
        "residual_risk_counts": residual_counts,
        "work_papers": papers_out,
        "advisory": (
            "Heuristic analysis only — human review required before "
            "regulatory or management reporting."
        ),
    }
