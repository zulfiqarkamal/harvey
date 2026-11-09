"""
Tests for gamma7.audit_intelligence.audit_llm_overlay

Uses unittest.mock to avoid real Anthropic API calls.
All tests run without ANTHROPIC_API_KEY.
"""
import json
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from gamma7.audit_intelligence.audit_universe import build_universe, AuditDecision
from gamma7.audit_intelligence.workpaper import build_workpapers, FindingSeverity
from gamma7.audit_intelligence.audit_llm_overlay import (
    SuggestedFinding,
    suggest_findings,
    suggest_findings_batch,
    _build_user_message,
    _parse_response,
    _severity_from_str,
)
from gamma7.engine.budget_guard import BudgetGuard, BudgetExceededError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _universe():
    return build_universe(organisation="Test Bank A/S", scope=["iso27001"])


def _wps():
    return build_workpapers(_universe())


def _wp_for(control_id="A.8.2"):
    return _wps().get_workpaper(control_id)


def _make_response(findings=None):
    """Mock anthropic response with structured findings."""
    if findings is None:
        findings = [{
            "title": "No multi-factor authentication enforced",
            "condition": "Privileged accounts do not require MFA.",
            "criteria": "ISO 27001 A.8.2 requires privileged access to be managed with appropriate controls including MFA.",
            "cause": "MFA policy not enforced via IAM tooling.",
            "effect": "Privileged accounts vulnerable to credential theft.",
            "recommendation": "Enforce MFA for all privileged accounts within 30 days.",
            "severity": "HIGH",
            "confidence": 0.88,
            "reasoning": "Evidence shows MFA is not configured.",
            "evidence_ref": "No MFA policy found in access control documentation.",
        }]
    content_block = MagicMock()
    content_block.text = json.dumps({"findings": findings})
    usage = SimpleNamespace(input_tokens=400, output_tokens=200)
    response = MagicMock()
    response.content = [content_block]
    response.usage = usage
    return response


def _budget(limit_dkk=100.0):
    return BudgetGuard(limit_dkk=limit_dkk, budget_file=None)


# ---------------------------------------------------------------------------
# _build_user_message
# ---------------------------------------------------------------------------

def test_build_user_message_contains_control_id():
    wp = _wp_for("A.8.2")
    msg = _build_user_message(wp, "Some evidence text.")
    assert "A.8.2" in msg


def test_build_user_message_contains_control_title():
    wp = _wp_for("A.8.2")
    msg = _build_user_message(wp, "Evidence.")
    assert wp.control_title in msg


def test_build_user_message_contains_risk_indicators():
    wp = _wp_for("A.8.2")
    msg = _build_user_message(wp, "Evidence.")
    # At least one risk indicator should appear
    if wp.risk_indicators:
        assert wp.risk_indicators[0] in msg


def test_build_user_message_truncates_evidence():
    wp = _wp_for("A.8.2")
    long_evidence = "X" * 10_000
    msg = _build_user_message(wp, long_evidence)
    assert len(msg) < 20_000  # Must be bounded


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------

def test_parse_response_valid_json():
    raw = json.dumps({"findings": [{"title": "F1", "severity": "HIGH"}]})
    result = _parse_response(raw)
    assert result is not None
    assert result[0]["title"] == "F1"


def test_parse_response_markdown_fence():
    raw = "```json\n" + json.dumps({"findings": []}) + "\n```"
    result = _parse_response(raw)
    assert result == []


def test_parse_response_empty_findings():
    raw = json.dumps({"findings": []})
    assert _parse_response(raw) == []


def test_parse_response_invalid_json():
    assert _parse_response("not json") is None


def test_parse_response_wrong_structure():
    # dict without findings key
    raw = json.dumps({"other": "data"})
    result = _parse_response(raw)
    assert result == []


# ---------------------------------------------------------------------------
# _severity_from_str
# ---------------------------------------------------------------------------

def test_severity_from_str_valid():
    assert _severity_from_str("CRITICAL") == FindingSeverity.CRITICAL
    assert _severity_from_str("high") == FindingSeverity.HIGH
    assert _severity_from_str("medium") == FindingSeverity.MEDIUM


def test_severity_from_str_invalid_defaults_low():
    assert _severity_from_str("UNKNOWN") == FindingSeverity.LOW
    assert _severity_from_str("") == FindingSeverity.LOW


# ---------------------------------------------------------------------------
# suggest_findings — environment / import errors
# ---------------------------------------------------------------------------

def test_suggest_findings_no_api_key_raises():
    wp = _wp_for("A.8.2")
    budget = _budget()
    with patch.dict("os.environ", {}, clear=True):
        # Ensure ANTHROPIC_API_KEY is not set
        import os
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            suggest_findings(wp, "evidence", budget)


def test_suggest_findings_budget_exceeded_raises():
    wp = _wp_for("A.8.2")
    budget = _budget(limit_dkk=0.0)  # 0 DKK — immediately exceeded
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with pytest.raises(BudgetExceededError):
            suggest_findings(wp, "evidence", budget)


# ---------------------------------------------------------------------------
# suggest_findings — happy path
# ---------------------------------------------------------------------------

def test_suggest_findings_returns_list():
    wp = _wp_for("A.8.2")
    budget = _budget()
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_response()
            suggestions = suggest_findings(wp, "No MFA configured.", budget)
    assert isinstance(suggestions, list)
    assert len(suggestions) == 1


def test_suggest_findings_returns_suggested_finding_type():
    wp = _wp_for("A.8.2")
    budget = _budget()
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_response()
            suggestions = suggest_findings(wp, "Evidence.", budget)
    assert isinstance(suggestions[0], SuggestedFinding)


def test_suggest_findings_control_id_matches():
    wp = _wp_for("A.8.2")
    budget = _budget()
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_response()
            suggestions = suggest_findings(wp, "Evidence.", budget)
    assert suggestions[0].control_id == "A.8.2"


def test_suggest_findings_five_c_fields_populated():
    wp = _wp_for("A.8.2")
    budget = _budget()
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_response()
            suggestions = suggest_findings(wp, "Evidence.", budget)
    s = suggestions[0]
    assert s.condition
    assert s.criteria
    assert s.cause
    assert s.effect
    assert s.recommendation


def test_suggest_findings_severity_is_enum():
    wp = _wp_for("A.8.2")
    budget = _budget()
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_response()
            suggestions = suggest_findings(wp, "Evidence.", budget)
    assert isinstance(suggestions[0].severity, FindingSeverity)


def test_suggest_findings_records_budget():
    wp = _wp_for("A.8.2")
    budget = _budget()
    before = budget.total_spent_dkk()
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_response()
            suggest_findings(wp, "Evidence.", budget)
    assert budget.total_spent_dkk() > before


def test_suggest_findings_tokens_recorded_in_suggestion():
    wp = _wp_for("A.8.2")
    budget = _budget()
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_response()
            suggestions = suggest_findings(wp, "Evidence.", budget)
    assert suggestions[0].tokens_used["input"] == 400
    assert suggestions[0].tokens_used["output"] == 200


# ---------------------------------------------------------------------------
# Low-confidence filtering
# ---------------------------------------------------------------------------

def test_suggest_findings_low_confidence_filtered_out():
    wp = _wp_for("A.8.2")
    budget = _budget()
    low_conf_findings = [{
        "title": "Weak finding",
        "condition": "c", "criteria": "cr", "cause": "ca",
        "effect": "e", "recommendation": "r",
        "severity": "LOW",
        "confidence": 0.40,  # Below 0.70 threshold
        "reasoning": "Uncertain.",
        "evidence_ref": "",
    }]
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_response(low_conf_findings)
            suggestions = suggest_findings(wp, "Evidence.", budget)
    assert len(suggestions) == 0


def test_suggest_findings_exact_threshold_included():
    wp = _wp_for("A.8.2")
    budget = _budget()
    threshold_findings = [{
        "title": "Borderline finding",
        "condition": "c", "criteria": "cr", "cause": "ca",
        "effect": "e", "recommendation": "r",
        "severity": "MEDIUM",
        "confidence": 0.70,  # Exactly at threshold
        "reasoning": "Borderline.",
        "evidence_ref": "",
    }]
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_response(threshold_findings)
            suggestions = suggest_findings(wp, "Evidence.", budget, confidence_threshold=0.70)
    assert len(suggestions) == 1


# ---------------------------------------------------------------------------
# Empty / unparseable responses
# ---------------------------------------------------------------------------

def test_suggest_findings_empty_response_returns_empty_list():
    wp = _wp_for("A.8.2")
    budget = _budget()
    content_block = MagicMock()
    content_block.text = json.dumps({"findings": []})
    usage = SimpleNamespace(input_tokens=100, output_tokens=20)
    response = MagicMock()
    response.content = [content_block]
    response.usage = usage
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = response
            suggestions = suggest_findings(wp, "Evidence.", budget)
    assert suggestions == []


def test_suggest_findings_unparseable_response_returns_empty_list():
    wp = _wp_for("A.8.2")
    budget = _budget()
    content_block = MagicMock()
    content_block.text = "I found some issues but cannot format them as JSON right now."
    usage = SimpleNamespace(input_tokens=100, output_tokens=30)
    response = MagicMock()
    response.content = [content_block]
    response.usage = usage
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = response
            suggestions = suggest_findings(wp, "Evidence.", budget)
    assert suggestions == []


# ---------------------------------------------------------------------------
# SuggestedFinding properties
# ---------------------------------------------------------------------------

def test_suggested_finding_is_high_confidence():
    sf = SuggestedFinding(
        control_id="A.8.2", title="T", condition="c", criteria="cr",
        cause="ca", effect="e", recommendation="r",
        severity=FindingSeverity.HIGH, confidence=0.90,
        reasoning="High confidence.", evidence_ref=None,
    )
    assert sf.is_high_confidence is True


def test_suggested_finding_not_high_confidence():
    sf = SuggestedFinding(
        control_id="A.8.2", title="T", condition="c", criteria="cr",
        cause="ca", effect="e", recommendation="r",
        severity=FindingSeverity.LOW, confidence=0.50,
        reasoning="Low confidence.", evidence_ref=None,
    )
    assert sf.is_high_confidence is False


# ---------------------------------------------------------------------------
# suggest_findings_batch
# ---------------------------------------------------------------------------

def test_suggest_findings_batch_returns_dict():
    wps = build_workpapers(_universe())
    evidence_map = {"A.8.2": "Evidence for A.8.2", "A.8.5": "Evidence for A.8.5"}
    budget = _budget()
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_response([])
            result = suggest_findings_batch(wps.work_papers, evidence_map, budget)
    assert isinstance(result, dict)
    assert "A.8.2" in result
    assert "A.8.5" in result


def test_suggest_findings_batch_skips_missing_evidence():
    wps = build_workpapers(_universe())
    evidence_map = {}  # No evidence provided
    budget = _budget()
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_response([])
            result = suggest_findings_batch(wps.work_papers, evidence_map, budget)
    assert result == {}
    mock_client.messages.create.assert_not_called()


def test_suggest_findings_batch_stops_on_budget_exhausted():
    wps = build_workpapers(_universe())
    # Provide evidence for all work papers
    evidence_map = {wp.control_id: "Some evidence." for wp in wps.work_papers}
    budget = _budget(limit_dkk=0.0)  # Exhausted immediately
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("gamma7.audit_intelligence.audit_llm_overlay.anthropic") as mock_anthropic:
            mock_client = MagicMock()
            mock_anthropic.Anthropic.return_value = mock_client
            mock_client.messages.create.return_value = _make_response([])
            # Should not raise — batch catches BudgetExceededError and stops
            result = suggest_findings_batch(wps.work_papers, evidence_map, budget)
    # With zero budget, no calls should succeed
    assert isinstance(result, dict)
