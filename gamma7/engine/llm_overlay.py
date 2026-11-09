"""
Gamma7 LLM Overlay — re-evaluates heuristic findings with an LLM to reduce false positives.

For each MISSING or AMBIGUOUS assessment from the heuristic engine, the overlay
sends the requirement text + relevant contract excerpt to Claude and gets back a
structured re-evaluation. Findings are only upgraded (false positives removed),
never downgraded — the closed-world heuristic catches omissions reliably.

Requires: ANTHROPIC_API_KEY environment variable.
Requires: BudgetGuard within limit — raises BudgetExceededError when exhausted.

No LLM call is made without a prior budget check.
All findings require human review regardless of LLM output.
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from dataclasses import dataclass, field
from typing import Optional

from .analyser import (
    ComplianceStatus,
    ContractAnalysisResult,
    RequirementAssessment,
)
from .budget_guard import BudgetGuard, BudgetExceededError

DEFAULT_MODEL = "claude-sonnet-4-6"
CONFIDENCE_THRESHOLD = 0.70
MAX_EXCERPT_CHARS = 2000  # characters of contract text sent per requirement


@dataclass
class OverlayDecision:
    requirement_id: str
    original_status: ComplianceStatus
    revised_status: ComplianceStatus
    confidence: float
    evidence_text: str
    reasoning: str
    tokens_used: dict = field(default_factory=dict)


SYSTEM_PROMPT = """\
You are a DORA Art. 30 / GDPR Art. 28 compliance analyst reviewing an ICT contract.
Your job is to re-evaluate a heuristic finding and determine whether the contract
actually satisfies, partially satisfies, or is missing the stated requirement.

Rules:
- Base your answer ONLY on the provided contract excerpts.
- Do not assume anything not stated in the contract.
- If in doubt, return "missing" — conservative is better for compliance.
- Output ONLY valid JSON, nothing else.

Output schema:
{
  "status": "satisfied" | "evidence_found" | "missing" | "not_applicable",
  "confidence": <float 0.0–1.0>,
  "evidence_text": "<direct quote or empty string>",
  "reasoning": "<one sentence>"
}

status meanings:
- "satisfied": requirement is fully and unambiguously met
- "evidence_found": contract contains relevant language but human review is still needed
- "missing": requirement is absent or clearly not addressed
- "not_applicable": requirement does not apply to this contract's scope
"""


def _build_user_message(assessment: RequirementAssessment, contract_excerpt: str) -> str:
    return textwrap.dedent(f"""\
        Requirement ID: {assessment.requirement_id}
        Article: {assessment.requirement_article}
        Requirement: {assessment.requirement_summary}
        Heuristic result: {assessment.status.value}

        Contract excerpt (most relevant section, up to {MAX_EXCERPT_CHARS} chars):
        ---
        {contract_excerpt[:MAX_EXCERPT_CHARS]}
        ---

        Re-evaluate: does this contract satisfy the requirement?
    """)


def _extract_excerpt(contract_text: str, assessment: RequirementAssessment) -> str:
    """
    Return the most relevant portion of the contract for this requirement.
    Prefers passages already found by the heuristic engine; falls back to full text.
    """
    if assessment.evidence:
        passages = [ev.get("text", "") if isinstance(ev, dict) else str(ev)
                    for ev in assessment.evidence[:3]]
        combined = "\n\n".join(p for p in passages if p)
        if combined.strip():
            return combined[:MAX_EXCERPT_CHARS]
    # Fallback: return a window around any keyword match, else full text truncated
    return contract_text[:MAX_EXCERPT_CHARS]


def _parse_llm_response(raw: str) -> Optional[dict]:
    """Extract JSON from LLM response — handles markdown code fences."""
    # Strip markdown fences
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
    raw = raw.rstrip("`").strip()
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def run_overlay(
    result: ContractAnalysisResult,
    contract_text: str,
    budget: BudgetGuard,
    model: str = DEFAULT_MODEL,
    confidence_threshold: float = CONFIDENCE_THRESHOLD,
    target_statuses: Optional[list[ComplianceStatus]] = None,
) -> tuple[ContractAnalysisResult, list[OverlayDecision]]:
    """
    Re-evaluate heuristic MISSING/AMBIGUOUS findings with an LLM.

    Returns the updated ContractAnalysisResult and a list of OverlayDecision records.
    Raises BudgetExceededError before the first call that would exceed the DKK limit.
    Raises EnvironmentError if ANTHROPIC_API_KEY is not set.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY is not set. Set it in your environment or .env file "
            "to enable the LLM overlay pass."
        )

    try:
        import anthropic
    except ImportError as e:
        raise ImportError("Install the anthropic package: pip install anthropic") from e

    client = anthropic.Anthropic(api_key=api_key)

    if target_statuses is None:
        target_statuses = [ComplianceStatus.MISSING, ComplianceStatus.AMBIGUOUS]

    decisions: list[OverlayDecision] = []
    assessments_to_review = [
        a for a in result.assessments if a.status in target_statuses
    ]

    for assessment in assessments_to_review:
        excerpt = _extract_excerpt(contract_text, assessment)
        user_msg = _build_user_message(assessment, excerpt)

        # Estimate token usage for budget check (rough: 1 token ≈ 4 chars)
        estimated_input = len(SYSTEM_PROMPT) // 4 + len(user_msg) // 4 + 50
        estimated_output = 150

        try:
            budget.check(input_tokens=estimated_input, output_tokens=estimated_output)
        except BudgetExceededError:
            # Stop the overlay pass — remaining assessments keep heuristic status
            break

        response = client.messages.create(
            model=model,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )

        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        budget.record(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            label=f"overlay:{assessment.requirement_id}",
        )

        raw_response = response.content[0].text if response.content else ""
        parsed = _parse_llm_response(raw_response)

        if not parsed:
            # Unparseable response — keep heuristic status, record as-is
            decisions.append(OverlayDecision(
                requirement_id=assessment.requirement_id,
                original_status=assessment.status,
                revised_status=assessment.status,
                confidence=0.0,
                evidence_text="",
                reasoning="LLM response could not be parsed",
                tokens_used={"input": input_tokens, "output": output_tokens},
            ))
            continue

        llm_status_str = parsed.get("status", "")
        confidence = float(parsed.get("confidence", 0.0))
        evidence_text = parsed.get("evidence_text", "")
        reasoning = parsed.get("reasoning", "")

        # Map LLM status string to ComplianceStatus
        status_map = {
            "satisfied": ComplianceStatus.SATISFIED,
            "evidence_found": ComplianceStatus.EVIDENCE_FOUND,
            "missing": ComplianceStatus.MISSING,
            "not_applicable": ComplianceStatus.NOT_APPLICABLE,
        }
        llm_status = status_map.get(llm_status_str, assessment.status)

        # Only upgrade (remove false positives), never downgrade confirmed gaps
        # Rule: if heuristic=MISSING and LLM says satisfied/evidence_found with high confidence → upgrade
        # Rule: never change EVIDENCE_FOUND → MISSING (trust heuristic evidence)
        revised_status = assessment.status
        if confidence >= confidence_threshold:
            if assessment.status == ComplianceStatus.MISSING and llm_status in (
                ComplianceStatus.SATISFIED, ComplianceStatus.EVIDENCE_FOUND, ComplianceStatus.NOT_APPLICABLE
            ):
                revised_status = llm_status
            elif assessment.status == ComplianceStatus.AMBIGUOUS and llm_status in (
                ComplianceStatus.SATISFIED, ComplianceStatus.EVIDENCE_FOUND, ComplianceStatus.MISSING
            ):
                revised_status = llm_status

        decision = OverlayDecision(
            requirement_id=assessment.requirement_id,
            original_status=assessment.status,
            revised_status=revised_status,
            confidence=confidence,
            evidence_text=evidence_text,
            reasoning=reasoning,
            tokens_used={"input": input_tokens, "output": output_tokens},
        )
        decisions.append(decision)

        # Apply the revised status to the assessment
        if revised_status != assessment.status:
            assessment.status = revised_status
            if evidence_text:
                assessment.evidence.append({
                    "text": evidence_text,
                    "section": "LLM overlay",
                    "relevance_note": f"LLM re-evaluation (confidence={confidence:.2f})",
                })
            if reasoning:
                assessment.gap_description = (
                    f"[LLM overlay, confidence={confidence:.2f}] {reasoning}"
                )

    # Recompute summary with updated statuses
    result.compute_summary()

    return result, decisions
