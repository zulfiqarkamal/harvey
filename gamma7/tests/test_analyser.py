"""
Deterministic unit tests for the gamma7 gap analysis engine.
Tests phrase-level matching, closed-world assumption, and scope filtering.

Run from gamma7/engine/ or project root:
    python -m pytest gamma7/tests/test_analyser.py -v
"""
import sys
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "engine"))

import pytest
from analyser import (
    _phrase_hits,
    _normalise,
    assess_requirement,
    ComplianceStatus,
    RiskLevel,
    _paragraph_number,
    _count_paragraphs,
    _scan_for_location,
)

# ── _phrase_hits ─────────────────────────────────────────────────────────────

def test_phrase_hits_exact_match():
    text = _normalise("The supplier shall maintain ISO 27001 certification.")
    assert "iso 27001" in _phrase_hits(text, ["iso 27001"])


def test_phrase_hits_no_partial_word_decomposition():
    # "audit" alone must NOT match the phrase "right to audit"
    text = _normalise("The contract covers auditing of financial records.")
    hits = _phrase_hits(text, ["right to audit"])
    assert hits == [], f"Expected no hit, got {hits}"


def test_phrase_hits_wildcard():
    text = _normalise("Supplier shall notify Customer within 24 hours of detection.")
    hits = _phrase_hits(text, ["notify.*within"])
    assert hits == ["notify.*within"]


def test_phrase_hits_case_insensitive():
    text = _normalise("RECOVERY TIME OBJECTIVE is 4 hours.")
    assert "recovery time objective" in _phrase_hits(text, ["recovery time objective"])


def test_phrase_hits_no_match_returns_empty():
    text = _normalise("This contract says nothing relevant.")
    assert _phrase_hits(text, ["threat-led penetration testing", "tlpt"]) == []


# ── assess_requirement helpers ───────────────────────────────────────────────

def _make_req(req_id, evidence_phrases, gap_phrases, scope="all", obligation="must"):
    return {
        "id": req_id,
        "article": "TEST",
        "text_en": f"Test requirement {req_id}",
        "scope": scope,
        "obligation_level": obligation,
        "category": "test",
        "evidence_criteria": [],
        "gap_indicators": [],
        "match_terms_evidence": evidence_phrases,
        "match_terms_gap": gap_phrases,
    }


# ── SATISFIED: evidence present, no gap ────────────────────────────────────

def test_evidence_found_when_evidence_present_no_gap():
    """Evidence found, no gap indicators → EVIDENCE_FOUND (conservative, not SATISFIED)."""
    req = _make_req("T-001", ["iso 27001"], [])
    contract = "Supplier maintains ISO 27001 certification for all services."
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status == ComplianceStatus.EVIDENCE_FOUND
    assert result.risk_level is None


# ── MISSING: no evidence, no gap (closed-world) ─────────────────────────────

def test_missing_closed_world_no_clauses():
    req = _make_req("T-002", ["threat-led penetration testing", "tlpt"], [])
    contract = "Supplier shall maintain standard security practices."
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status == ComplianceStatus.MISSING
    assert "absent" in result.gap_description.lower() or "no relevant" in result.gap_description.lower()


# ── MISSING: gap indicator only, no evidence ────────────────────────────────

def test_missing_when_gap_indicator_only():
    req = _make_req("T-003", ["recovery time objective"], ["commercially reasonable efforts to maintain"])
    contract = "Supplier will use commercially reasonable efforts to maintain availability."
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status == ComplianceStatus.MISSING


# ── AMBIGUOUS: both evidence and gap ────────────────────────────────────────

def test_ambiguous_when_evidence_and_gap():
    req = _make_req("T-004", ["recovery time objective"], ["commercially reasonable efforts"])
    contract = "Recovery Time Objective is targeted at 8 hours using commercially reasonable efforts."
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status == ComplianceStatus.AMBIGUOUS


# ── SCOPE: critical_only skipped for non_critical ────────────────────────────

def test_na_when_scope_critical_only_and_non_critical():
    req = _make_req("T-005", ["tlpt"], [], scope="critical_only")
    contract = "No TLPT clause here."
    result = assess_requirement(req, contract, _normalise(contract), "non_critical")
    assert result.status == ComplianceStatus.NOT_APPLICABLE


def test_assessed_when_scope_critical_only_and_critical():
    req = _make_req("T-006", ["threat-led penetration testing"], [], scope="critical_only")
    contract = "No TLPT clause here."
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status == ComplianceStatus.MISSING


# ── False-positive prevention ────────────────────────────────────────────────

def test_no_fp_on_good_contract_tlpt():
    """ART30-009: contract with TLPT clause → EVIDENCE_FOUND (conservative, requires human review)."""
    req = _make_req("ART30-009", ["threat-led penetration testing", "tlpt"], [], scope="critical_only")
    contract = (
        "Clause 7: Threat-Led Penetration Testing (TLPT). "
        "SecureVault cooperates fully with TLPT exercises."
    )
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status == ComplianceStatus.EVIDENCE_FOUND


def test_no_fp_on_good_contract_audit_rights():
    """ART30-010: contract with explicit audit rights, no blocking language → AMBIGUOUS (45-day notice)."""
    req = _make_req(
        "ART30-010",
        ["right to audit", "third-party auditor"],
        ["not operationally feasible", "not available for direct customer audit", "45 days"],
    )
    contract = (
        "Customer has the right to audit SecureVault with 45 days advance notice. "
        "Third-party auditor may be used."
    )
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    # 45 days is a gap indicator → AMBIGUOUS, not false positive
    assert result.status == ComplianceStatus.AMBIGUOUS


def test_no_fp_audit_rights_no_blocking():
    """ART30-010 with audit rights but no blocking gap phrase → EVIDENCE_FOUND (conservative)."""
    req = _make_req(
        "ART30-010",
        ["right to audit", "third-party auditor"],
        ["not operationally feasible", "not available for direct customer audit", "45 days"],
    )
    contract = (
        "Customer has the right to audit Supplier with 30 days advance notice. "
        "A third-party auditor appointed by Customer may conduct the audit."
    )
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status == ComplianceStatus.EVIDENCE_FOUND


# ── Evidence passage extraction ───────────────────────────────────────────────

def test_evidence_passages_extracted():
    req = _make_req("T-007", ["iso 27001"], [])
    contract = "Section 4: Security. Supplier holds ISO 27001 certification reviewed annually."
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert len(result.evidence) >= 1
    assert "iso 27001" in result.evidence[0]["text"].lower()


# ── Held-out tests (language NOT derived from the 5 synthetic training contracts) ─────

def test_held_out_evidence_found_not_satisfied():
    """Held-out: evidence found → EVIDENCE_FOUND (conservative, not SATISFIED)."""
    req = _make_req("T-H01", ["exit assistance", "transition support"], [])
    contract = "Provider shall offer exit assistance and transition support for 90 days after termination."
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status == ComplianceStatus.EVIDENCE_FOUND
    assert len(result.evidence) > 0


def test_held_out_conditional_no_trigger_not_assessed():
    """Held-out: conditional scope, no trigger detected → NOT_ASSESSED (not NOT_APPLICABLE)."""
    req = _make_req(
        "T-H02",
        ["standard contractual clauses", "adequacy decision"],
        ["some processing may involve transfers"],
        scope="conditional",
    )
    contract = "All data processing occurs within the European Economic Area."
    result = assess_requirement(req, contract, _normalise(contract), "non_critical")
    assert result.status == ComplianceStatus.NOT_ASSESSED


def test_held_out_bcp_critical_only_non_critical_na():
    """Held-out: critical_only scope on non_critical contract → NOT_APPLICABLE."""
    req = _make_req(
        "T-H03",
        ["test the bcp and drp", "test the bcp"],
        ["reviews but does not test", "annual review of the bcp"],
        scope="critical_only",
    )
    contract = "Provider shall test the BCP and DRP on an annual basis."
    result = assess_requirement(req, contract, _normalise(contract), "non_critical")
    assert result.status == ComplianceStatus.NOT_APPLICABLE


def test_held_out_bcp_review_not_testing_critical():
    """Held-out: gap indicator 'annual review of the bcp' present without testing evidence → MISSING or AMBIGUOUS."""
    req = _make_req(
        "T-H04",
        ["test the bcp and drp", "test the bcp", "test the drp", "live failover"],
        ["reviews but does not test", "testing would be disruptive", "annual review of the bcp"],
        scope="critical_only",
    )
    contract = "Provider conducts an annual review of the BCP and updates it as required by management."
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status in (ComplianceStatus.MISSING, ComplianceStatus.AMBIGUOUS)


def test_held_out_audit_no_rights_missing():
    """Held-out: no audit rights language at all → MISSING (closed-world assumption)."""
    req = _make_req(
        "T-H05",
        ["right to audit", "third-party auditor", "on-site audit"],
        ["not operationally feasible", "not available for direct customer audit"],
    )
    contract = "Provider shall maintain ISO 27001 certification and provide quarterly security reports."
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status == ComplianceStatus.MISSING


# ── Source citation helpers ───────────────────────────────────────────────────

def test_paragraph_number_first_paragraph():
    contract = "First paragraph here.\n\nSecond paragraph.\n\nThird paragraph."
    # offset 0 is in paragraph 1
    assert _paragraph_number(contract, 0) == 1


def test_paragraph_number_second_paragraph():
    contract = "First paragraph here.\n\nSecond paragraph.\n\nThird paragraph."
    # offset after the first double-newline
    offset = contract.index("Second")
    assert _paragraph_number(contract, offset) == 2


def test_count_paragraphs():
    contract = "Para one.\n\nPara two.\n\nPara three."
    assert _count_paragraphs(contract) == 3


def test_scan_for_location_finds_heading():
    contract = "## Audit Rights\n\nThe supplier shall allow audits.\n\n## Termination\n\nEither party may terminate."
    loc = _scan_for_location(contract, ["right to audit", "audit rights"])
    assert "Audit Rights" in loc


def test_scan_for_location_no_heading_fallback():
    contract = "The supplier shall maintain certifications.\n\nAll other matters are governed by law."
    loc = _scan_for_location(contract, ["right to audit", "third-party auditor"])
    assert "full contract" in loc
    assert "¶" in loc


def test_source_location_populated_for_missing():
    req = _make_req(
        "T-SRC1",
        ["right to audit"],
        [],
    )
    contract = "The supplier shall maintain ISO 27001.\n\nAll services are delivered securely."
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status == ComplianceStatus.MISSING
    assert result.source_location is not None
    assert "¶" in result.source_location or "full contract" in result.source_location


def test_source_location_populated_for_evidence_found():
    req = _make_req(
        "T-SRC2",
        ["right to audit"],
        [],
    )
    contract = "## Audit Rights\n\nThe customer has the right to audit the supplier at any time.\n\nAll fees are non-refundable."
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status == ComplianceStatus.EVIDENCE_FOUND
    assert result.source_location is not None
    # Should reference the section heading
    assert "Audit Rights" in result.source_location or "¶" in result.source_location


def test_evidence_passage_has_paragraph_num():
    req = _make_req(
        "T-SRC3",
        ["right to audit"],
        [],
    )
    contract = "## Intro\n\nGeneral terms.\n\n## Audit Rights\n\nThe customer has the right to audit."
    result = assess_requirement(req, contract, _normalise(contract), "critical_important")
    assert result.status == ComplianceStatus.EVIDENCE_FOUND
    assert len(result.evidence) > 0
    assert result.evidence[0]["paragraph_num"] is not None
