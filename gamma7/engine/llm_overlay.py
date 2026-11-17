"""
Gamma7 LLM Overlay — two passes over heuristic findings.

Pass 1 (run_overlay): re-evaluates MISSING/AMBIGUOUS findings upward.
  For each MISSING or AMBIGUOUS assessment, sends the requirement + contract
  excerpt to Claude and potentially upgrades the status. Findings are only
  upgraded, never downgraded — the closed-world heuristic catches omissions.

Pass 2 (run_evidence_validation_overlay): validates EVIDENCE_FOUND context.
  Some requirements produce false negatives when generic keywords (e.g.
  "written notice") match DPA/subcontracting clauses rather than the ICT
  monitoring/incident clauses that the requirement actually covers. This pass
  applies deterministic section-context rules (no API key needed) and, when
  ANTHROPIC_API_KEY is set, an optional LLM semantic check. Assessments that
  fail the context check are downgraded from EVIDENCE_FOUND to AMBIGUOUS.

Requires: ANTHROPIC_API_KEY environment variable (optional for Pass 2).
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


# ---------------------------------------------------------------------------
# Pass 2 — contextual evidence validation for specific requirements
# ---------------------------------------------------------------------------

# Requirement IDs known to produce false negatives because generic keywords
# (e.g. "written notice", "within 24 hours") match unrelated contract sections.
# Each entry defines:
#   "section_bad":    patterns that indicate a passage is from the wrong context
#   "section_good":   patterns that indicate a passage is from the right context
#   "passage_good":   body-text patterns that confirm the passage is relevant
#   "downgrade_reason": gap description when downgraded
#   "llm_question":   targeted question for the optional LLM semantic check
_EVIDENCE_VALIDATION_SPECS: dict[str, dict] = {
    "ART30-008": {
        # Incident notification requirement: evidence must come from incident/
        # notification sections AND the body must explicitly reference ICT incidents
        # or regulatory/supervisory reporting.  "Written incident notification" alone
        # (e.g. "notify Client's incident response team") is not enough — DORA
        # Art. 30(2)(h) requires obligation to support the financial entity in
        # regulatory incident reporting to supervisors.
        "section_bad": re.compile(
            r"access.{0,20}management|access.{0,20}control|hr |human.?resource|"
            r"access.{0,20}provi|user.{0,20}access|least.{0,20}privilege",
            re.IGNORECASE,
        ),
        "section_good": re.compile(
            r"incident|notification|breach|escalat|report|ict|security.{0,20}event",
            re.IGNORECASE,
        ),
        "passage_good": re.compile(
            r"ict.{0,30}incident|major.{0,20}incident|regulatory.{0,30}report|"
            r"supervis|dora|incident.{0,30}manag",
            re.IGNORECASE,
        ),
        # For this requirement a good section is NOT enough on its own — the
        # passage body must also confirm ICT/regulatory scope.
        # NOTE: "incident.{0,30}notif" and "notif.{0,60}incident" were removed:
        # generic "Written incident notification to Client's incident response team"
        # (provider-side severity-scheme notifications) does NOT satisfy DORA
        # Art. 30(2)(h), which requires ICT-specific incidents AND support for
        # the financial entity's regulatory reporting to supervisors.
        "require_passage_in_good_section": True,
        "downgrade_reason": (
            "Evidence found but passages do not clearly cover ICT incident notification "
            "AND the obligation to support the financial entity's regulatory incident "
            "reporting — human legal review required. DORA Art. 30(2)(h) requires both."
        ),
        "llm_question": (
            "Does the contract contain (1) an obligation to notify the financial entity "
            "of ICT incidents (not only personal data breaches), AND (2) an obligation to "
            "assist the financial entity in managing/reporting those incidents to regulators? "
            "Answer based ONLY on the contract excerpts. Reply 'yes', 'partial', or 'no'."
        ),
    },
    "ART30-012": {
        # Notice periods / proactive reporting for monitoring: evidence must NOT
        # come exclusively from DPA subprocessor or data-deletion sections.
        "section_bad": re.compile(
            r"subprocessor|sub.?processor|subcontract|sub.?contract|"
            r"data.{0,20}delet|data.{0,20}return|gdpr|data.{0,20}process",
            re.IGNORECASE,
        ),
        "section_good": re.compile(
            r"monitoring|reporting|service.{0,20}level|performance|change.{0,20}manag|"
            r"notice.{0,20}period|service.{0,20}change|operation|audit|transparenc|"
            r"terminat|exit|transfer",
            re.IGNORECASE,
        ),
        "passage_good": re.compile(
            r"monitor.{0,40}service|service.{0,40}report|proactive.{0,30}report|"
            r"operational.{0,30}report|service.{0,30}change|planned.{0,30}change|"
            r"material.{0,30}change|change.{0,30}notice|notice.{0,30}period",
            re.IGNORECASE,
        ),
        "downgrade_reason": (
            "Evidence found but all matching passages appear to be DPA/subprocessor "
            "clauses rather than general proactive reporting or service-change notice "
            "obligations. DORA Art. 30(3)(b) requires notice periods and reporting "
            "obligations that enable the financial entity to effectively monitor ICT "
            "services — event-specific DPA notices do not satisfy this."
        ),
        "llm_question": (
            "Does the contract contain a GENERAL obligation for the supplier to "
            "proactively report on ICT service performance to enable the financial entity "
            "to monitor services? Note: notice periods that apply only to sub-processor "
            "changes or data deletion do NOT satisfy this requirement. Reply 'yes', "
            "'partial', or 'no'."
        ),
    },
}


@dataclass
class ValidationDecision:
    requirement_id: str
    original_status: ComplianceStatus
    revised_status: ComplianceStatus
    rule_applied: str
    reasoning: str
    llm_used: bool = False
    tokens_used: dict = field(default_factory=dict)


def _classify_evidence_context(
    evidence: list[dict],
    spec: dict,
) -> tuple[bool, str]:
    """
    Deterministic context check for a list of evidence passages.

    Returns (evidence_is_valid, rule_name) where evidence_is_valid=True means
    at least one passage passes the context check and the assessment should NOT
    be downgraded.
    """
    section_bad = spec["section_bad"]
    section_good = spec["section_good"]
    passage_good = spec["passage_good"]
    require_passage = spec.get("require_passage_in_good_section", False)

    good_count = 0
    bad_count = 0

    for ev in evidence:
        section = ev.get("section", "") if isinstance(ev, dict) else ""
        text = ev.get("text", "") if isinstance(ev, dict) else str(ev)
        combined = f"{section} {text}"

        section_matches_bad = bool(section_bad.search(section))
        section_matches_good = bool(section_good.search(section))
        passage_matches_good = bool(passage_good.search(combined))

        if section_matches_bad and not section_matches_good:
            bad_count += 1
        elif section_matches_good:
            if require_passage and not passage_matches_good:
                # Right section but body doesn't confirm required semantic content
                bad_count += 1
            else:
                good_count += 1
        elif passage_matches_good:
            good_count += 1
        else:
            bad_count += 1

    if good_count > 0:
        return True, f"good_evidence={good_count}"
    if bad_count > 0:
        return False, f"all_bad_context={bad_count}"
    return False, "no_relevant_evidence"


def run_evidence_validation_overlay(
    result: ContractAnalysisResult,
    contract_text: str,
    budget: Optional[BudgetGuard] = None,
    model: str = DEFAULT_MODEL,
    use_llm: bool = True,
) -> tuple[ContractAnalysisResult, list[ValidationDecision]]:
    """
    Pass 2: validate context of EVIDENCE_FOUND assessments for requirements
    prone to keyword false negatives (ART30-008, ART30-012).

    Deterministic rules run first. When ANTHROPIC_API_KEY is set and use_llm=True,
    ambiguous cases escalate to an LLM check. Downgrade is EVIDENCE_FOUND →
    AMBIGUOUS only; nothing is changed to MISSING.

    No LLM calls are made when ANTHROPIC_API_KEY is not set.
    """
    decisions: list[ValidationDecision] = []

    for assessment in result.assessments:
        if assessment.status != ComplianceStatus.EVIDENCE_FOUND:
            continue
        spec = _EVIDENCE_VALIDATION_SPECS.get(assessment.requirement_id)
        if spec is None:
            continue

        evidence = [
            ev if isinstance(ev, dict) else {"text": str(ev), "section": ""}
            for ev in assessment.evidence
        ]
        is_valid, rule = _classify_evidence_context(evidence, spec)

        if is_valid:
            # Evidence looks contextually correct — no change
            decisions.append(ValidationDecision(
                requirement_id=assessment.requirement_id,
                original_status=assessment.status,
                revised_status=assessment.status,
                rule_applied=f"context_ok:{rule}",
                reasoning="Evidence section context matches requirement",
            ))
            continue

        # Deterministic rule says evidence is in wrong context.
        # Try LLM escalation if API key is available and budget allows.
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        llm_confirmed_bad = False
        tokens_used: dict = {}

        if use_llm and api_key and budget is not None:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=api_key)

                excerpt = _extract_excerpt(contract_text, assessment)
                llm_question = spec["llm_question"]
                user_msg = (
                    f"Requirement: {assessment.requirement_id} — "
                    f"{assessment.requirement_summary}\n\n"
                    f"Contract excerpt:\n---\n{excerpt[:MAX_EXCERPT_CHARS]}\n---\n\n"
                    f"{llm_question}"
                )
                est_in = (len(user_msg) + 300) // 4
                budget.check(input_tokens=est_in, output_tokens=50)

                resp = client.messages.create(
                    model=model,
                    max_tokens=100,
                    system=(
                        "You are a DORA Art. 30 compliance analyst. "
                        "Answer ONLY 'yes', 'partial', or 'no'. No other text."
                    ),
                    messages=[{"role": "user", "content": user_msg}],
                )
                tokens_used = {
                    "input": resp.usage.input_tokens,
                    "output": resp.usage.output_tokens,
                }
                budget.record(
                    input_tokens=resp.usage.input_tokens,
                    output_tokens=resp.usage.output_tokens,
                    label=f"validation:{assessment.requirement_id}",
                )
                answer = resp.content[0].text.strip().lower() if resp.content else ""
                if answer.startswith("no"):
                    llm_confirmed_bad = True
                # 'partial' → downgrade to AMBIGUOUS as well (partial evidence)
                elif answer.startswith("partial"):
                    llm_confirmed_bad = True

            except (BudgetExceededError, ImportError, Exception):
                # LLM unavailable or budget exhausted — fall back to deterministic rule
                llm_confirmed_bad = True

        elif not (use_llm and api_key):
            # No LLM available — trust deterministic rule
            llm_confirmed_bad = True

        if llm_confirmed_bad:
            assessment.status = ComplianceStatus.AMBIGUOUS
            assessment.gap_description = spec["downgrade_reason"]
            decisions.append(ValidationDecision(
                requirement_id=assessment.requirement_id,
                original_status=ComplianceStatus.EVIDENCE_FOUND,
                revised_status=ComplianceStatus.AMBIGUOUS,
                rule_applied=f"context_invalid:{rule}",
                reasoning=spec["downgrade_reason"],
                llm_used=bool(tokens_used),
                tokens_used=tokens_used,
            ))
        else:
            # LLM said yes/partial but we're uncertain — keep EVIDENCE_FOUND
            decisions.append(ValidationDecision(
                requirement_id=assessment.requirement_id,
                original_status=assessment.status,
                revised_status=assessment.status,
                rule_applied=f"llm_confirmed_ok:{rule}",
                reasoning="LLM review confirmed evidence is contextually relevant",
                llm_used=True,
                tokens_used=tokens_used,
            ))

    result.compute_summary()
    return result, decisions
