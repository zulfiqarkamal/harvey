"""
Gamma7 Audit Intelligence — LLM Finding Suggester
===================================================
Given a WorkPaper and one or more evidence texts, asks Claude to identify
potential control-testing findings using the IIA 5-C format.

Returns SUGGESTED findings only. Nothing is written to the WorkPaper
automatically — the auditor must call wp.add_finding() after reviewing each
suggestion. This preserves the human-in-the-loop requirement.

Requires: ANTHROPIC_API_KEY environment variable.
Requires: BudgetGuard within limit — raises BudgetExceededError before any
          call that would exceed the DKK limit.

No finding is ever committed without human approval.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from dataclasses import dataclass, field
from typing import Optional

from .workpaper import WorkPaper, FindingSeverity
from ..engine.budget_guard import BudgetGuard, BudgetExceededError

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore[assignment]

DEFAULT_MODEL = "claude-sonnet-4-6"
CONFIDENCE_THRESHOLD = 0.70
MAX_EVIDENCE_CHARS = 3000  # chars of evidence sent per control


# ---------------------------------------------------------------------------
# Output type — suggested, not yet committed
# ---------------------------------------------------------------------------

@dataclass
class SuggestedFinding:
    """
    A finding suggested by the LLM overlay. Must be reviewed and approved
    by the auditor before being added to a WorkPaper via wp.add_finding().
    """
    control_id: str
    title: str

    # IIA 5-C
    condition: str
    criteria: str
    cause: str
    effect: str
    recommendation: str

    severity: FindingSeverity
    confidence: float
    reasoning: str                   # LLM's explanation of the finding
    evidence_ref: Optional[str] = None  # Quote or section from evidence text
    tokens_used: dict = field(default_factory=dict)

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= CONFIDENCE_THRESHOLD


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an experienced IT internal auditor reviewing evidence for ISO 27001:2022 Annex A controls.
Your task is to identify whether the evidence reveals a control deficiency that should be documented
as an audit finding, using the IIA five-element finding format.

Rules:
- Base your analysis ONLY on the evidence provided.
- Do not invent observations not supported by the evidence.
- If no deficiency is apparent, return an empty findings array.
- Be conservative: a minor gap is LOW, a systemic failure is CRITICAL.
- Output ONLY valid JSON, nothing else.

Output schema:
{
  "findings": [
    {
      "title": "<short finding title>",
      "condition": "<what IS: factual observation from evidence>",
      "criteria": "<what SHOULD BE: cite the ISO 27001 control requirement>",
      "cause": "<root cause: why the gap exists>",
      "effect": "<so what: risk or impact>",
      "recommendation": "<how to fix it, specific and time-bound if possible>",
      "severity": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW" | "INFO",
      "confidence": <float 0.0–1.0>,
      "reasoning": "<one-sentence explanation>",
      "evidence_ref": "<direct quote from evidence or empty string>"
    }
  ]
}

Severity guide:
- CRITICAL: systemic failure, imminent breach or regulatory sanction risk
- HIGH: significant gap, likely exploitation or audit qualification without remediation
- MEDIUM: partial failure, mitigating controls present, manageable risk
- LOW: minor deviation, negligible residual risk
- INFO: best-practice observation, not a deficiency
"""


def _build_user_message(wp: WorkPaper, evidence_text: str) -> str:
    objectives = wp.audit_objective or "No objective provided."
    risk_indicators = "\n".join(f"- {r}" for r in wp.risk_indicators[:5]) or "None listed."
    expected_evidence = "\n".join(f"- {a}" for a in wp.expected_evidence_artifacts[:5]) or "None listed."

    return textwrap.dedent(f"""\
        Control ID: {wp.control_id}
        Control Title: {wp.control_title}
        Domain: {wp.control_domain}
        Inherent Risk: {wp.inherent_risk}

        Audit Objective: {objectives}

        Risk Indicators to watch for:
        {risk_indicators}

        Expected Evidence Artifacts:
        {expected_evidence}

        Evidence provided by auditor (up to {MAX_EVIDENCE_CHARS} chars):
        ---
        {evidence_text[:MAX_EVIDENCE_CHARS]}
        ---

        Review the evidence and identify any control deficiencies as audit findings.
        If the evidence is satisfactory and no gaps are apparent, return an empty findings array.
    """)


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _parse_response(raw: str) -> Optional[list[dict]]:
    """Extract findings list from LLM JSON response. Handles markdown fences."""
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data.get("findings", [])
        return None
    except json.JSONDecodeError:
        return None


def _severity_from_str(s: str) -> FindingSeverity:
    try:
        return FindingSeverity(s.upper())
    except ValueError:
        return FindingSeverity.LOW


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_findings(
    wp: WorkPaper,
    evidence_text: str,
    budget: BudgetGuard,
    model: str = DEFAULT_MODEL,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> list[SuggestedFinding]:
    """
    Ask Claude to identify potential findings for a single WorkPaper.

    Returns a list of SuggestedFinding. The caller must review each one
    and call wp.add_finding() for those they accept.

    Raises:
        EnvironmentError: ANTHROPIC_API_KEY not set.
        BudgetExceededError: DKK limit would be exceeded.
        ImportError: anthropic package not installed.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Set it in your environment or .env file."
        )

    if anthropic is None:
        raise ImportError("Install the anthropic package: pip install anthropic")

    client = anthropic.Anthropic(api_key=api_key)
    user_msg = _build_user_message(wp, evidence_text)

    # Pre-flight budget check (rough estimate: 1 token ≈ 4 chars)
    estimated_input = (len(SYSTEM_PROMPT) + len(user_msg)) // 4 + 50
    estimated_output = 500
    budget.check(input_tokens=estimated_input, output_tokens=estimated_output)

    response = client.messages.create(
        model=model,
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    input_tokens = response.usage.input_tokens
    output_tokens = response.usage.output_tokens
    budget.record(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        label=f"audit_overlay:{wp.control_id}",
    )

    raw = response.content[0].text if response.content else ""
    parsed = _parse_response(raw)

    if parsed is None:
        return []

    suggestions: list[SuggestedFinding] = []
    for item in parsed:
        confidence = float(item.get("confidence", 0.0))
        if confidence < confidence_threshold:
            continue
        suggestions.append(SuggestedFinding(
            control_id=wp.control_id,
            title=item.get("title", "Untitled finding"),
            condition=item.get("condition", ""),
            criteria=item.get("criteria", ""),
            cause=item.get("cause", ""),
            effect=item.get("effect", ""),
            recommendation=item.get("recommendation", ""),
            severity=_severity_from_str(item.get("severity", "LOW")),
            confidence=confidence,
            reasoning=item.get("reasoning", ""),
            evidence_ref=item.get("evidence_ref") or None,
            tokens_used={"input": input_tokens, "output": output_tokens},
        ))

    return suggestions


def suggest_findings_batch(
    work_papers: list[WorkPaper],
    evidence_map: dict[str, str],
    budget: BudgetGuard,
    model: str = DEFAULT_MODEL,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
) -> dict[str, list[SuggestedFinding]]:
    """
    Run suggest_findings across multiple WorkPapers.

    Args:
        work_papers: WorkPapers to analyse.
        evidence_map: control_id → evidence text. WorkPapers not in the map
                      are skipped.
        budget: Shared BudgetGuard — stops the batch when exhausted.

    Returns:
        Dict mapping control_id → list[SuggestedFinding].
    """
    results: dict[str, list[SuggestedFinding]] = {}
    for wp in work_papers:
        evidence = evidence_map.get(wp.control_id)
        if not evidence:
            continue
        try:
            suggestions = suggest_findings(
                wp, evidence, budget, model=model,
                confidence_threshold=confidence_threshold,
            )
            results[wp.control_id] = suggestions
        except BudgetExceededError:
            break
    return results
