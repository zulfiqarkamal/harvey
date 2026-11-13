"""
Tests for gamma7.engine.llm_overlay

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

from gamma7.engine.analyser import ComplianceStatus, ContractAnalysisResult, RequirementAssessment
from gamma7.engine.budget_guard import BudgetGuard, BudgetExceededError
from gamma7.engine.llm_overlay import (
    OverlayDecision,
    _build_user_message,
    _extract_excerpt,
    _parse_llm_response,
    run_overlay,
)


# --- Helpers ---

def _make_assessment(req_id="ART30-001", status=ComplianceStatus.MISSING, evidence=None):
    return RequirementAssessment(
        requirement_id=req_id,
        requirement_article="DORA Art. 30",
        requirement_summary="Test requirement",
        status=status,
        evidence=evidence or [],
        gap_description="Gap detected" if status == ComplianceStatus.MISSING else "",
    )


def _make_result(assessments=None):
    result = MagicMock(spec=ContractAnalysisResult)
    result.assessments = assessments if assessments is not None else [_make_assessment()]
    result.compute_summary = MagicMock()
    return result


def _make_response(status="evidence_found", confidence=0.85, evidence_text="Found clause.", reasoning="Clause present."):
    content_block = MagicMock()
    content_block.text = json.dumps({
        "status": status,
        "confidence": confidence,
        "evidence_text": evidence_text,
        "reasoning": reasoning,
    })
    usage = MagicMock()
    usage.input_tokens = 500
    usage.output_tokens = 100
    response = MagicMock()
    response.content = [content_block]
    response.usage = usage
    return response


# --- _build_user_message ---

def test_build_user_message_contains_requirement_id():
    a = _make_assessment(req_id="ART30-005")
    msg = _build_user_message(a, "excerpt text")
    assert "ART30-005" in msg


def test_build_user_message_contains_excerpt():
    a = _make_assessment()
    msg = _build_user_message(a, "this is the excerpt")
    assert "this is the excerpt" in msg


def test_build_user_message_contains_heuristic_status():
    a = _make_assessment(status=ComplianceStatus.MISSING)
    msg = _build_user_message(a, "excerpt")
    assert "missing" in msg.lower()


# --- _extract_excerpt ---

def test_extract_excerpt_uses_evidence_when_available():
    a = _make_assessment(evidence=[{"text": "Direct clause text"}])
    result = _extract_excerpt("full contract text", a)
    assert "Direct clause text" in result


def test_extract_excerpt_falls_back_to_contract_text():
    a = _make_assessment(evidence=[])
    contract = "A" * 3000
    result = _extract_excerpt(contract, a)
    assert len(result) <= 2000


def test_extract_excerpt_handles_string_evidence():
    a = _make_assessment(evidence=["plain string evidence"])
    result = _extract_excerpt("full text", a)
    assert "plain string evidence" in result


# --- _parse_llm_response ---

def test_parse_valid_json():
    raw = '{"status": "satisfied", "confidence": 0.9, "evidence_text": "clause", "reasoning": "clear"}'
    parsed = _parse_llm_response(raw)
    assert parsed is not None
    assert parsed["status"] == "satisfied"
    assert parsed["confidence"] == 0.9


def test_parse_strips_markdown_fences():
    raw = '```json\n{"status": "missing", "confidence": 0.5, "evidence_text": "", "reasoning": "absent"}\n```'
    parsed = _parse_llm_response(raw)
    assert parsed is not None
    assert parsed["status"] == "missing"


def test_parse_returns_none_on_invalid_json():
    assert _parse_llm_response("not json at all") is None


def test_parse_returns_none_on_non_dict():
    assert _parse_llm_response("[1, 2, 3]") is None


# --- run_overlay: environment check ---

def test_run_overlay_raises_without_api_key(tmp_path):
    result = _make_result()
    budget = BudgetGuard(budget_file=tmp_path / "b.json")
    with patch.dict("os.environ", {}, clear=True):
        # Remove ANTHROPIC_API_KEY if set
        import os
        os.environ.pop("ANTHROPIC_API_KEY", None)
        with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
            run_overlay(result, "contract text", budget)


# --- run_overlay: budget enforcement ---

def test_run_overlay_stops_when_budget_exceeded(tmp_path):
    """BudgetGuard with tiny limit stops the overlay before the first API call."""
    budget = BudgetGuard(limit_dkk=0.00001, budget_file=tmp_path / "b.json")
    assessments = [
        _make_assessment("R1", ComplianceStatus.MISSING),
        _make_assessment("R2", ComplianceStatus.MISSING),
    ]
    result = _make_result(assessments)

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            updated, decisions = run_overlay(result, "contract", budget)
    # With a vanishingly small budget, no calls should complete
    assert mock_client.messages.create.call_count == 0


# --- run_overlay: only upgrades, never downgrades ---

def test_overlay_upgrades_missing_to_evidence_found(tmp_path):
    budget = BudgetGuard(limit_dkk=200.0, budget_file=tmp_path / "b.json")
    a = _make_assessment("R1", ComplianceStatus.MISSING)
    result = _make_result([a])

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.messages.create.return_value = _make_response(
                status="evidence_found", confidence=0.9
            )
            updated, decisions = run_overlay(result, "contract text with clause", budget)

    assert len(decisions) == 1
    assert decisions[0].original_status == ComplianceStatus.MISSING
    assert decisions[0].revised_status == ComplianceStatus.EVIDENCE_FOUND


def test_overlay_does_not_downgrade_evidence_found(tmp_path):
    budget = BudgetGuard(limit_dkk=200.0, budget_file=tmp_path / "b.json")
    a = _make_assessment("R1", ComplianceStatus.MISSING)
    result = _make_result([a])

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            # LLM says missing with high confidence but original was MISSING, so stays MISSING
            mock_client.messages.create.return_value = _make_response(
                status="missing", confidence=0.95
            )
            _, decisions = run_overlay(result, "contract", budget)

    assert decisions[0].revised_status == ComplianceStatus.MISSING


def test_overlay_requires_confidence_threshold_to_upgrade(tmp_path):
    budget = BudgetGuard(limit_dkk=200.0, budget_file=tmp_path / "b.json")
    a = _make_assessment("R1", ComplianceStatus.MISSING)
    result = _make_result([a])

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            # Confidence below threshold (0.70) — should NOT upgrade
            mock_client.messages.create.return_value = _make_response(
                status="satisfied", confidence=0.50
            )
            _, decisions = run_overlay(result, "contract", budget)

    assert decisions[0].revised_status == ComplianceStatus.MISSING


def test_overlay_skips_satisfied_assessments(tmp_path):
    budget = BudgetGuard(limit_dkk=200.0, budget_file=tmp_path / "b.json")
    a = _make_assessment("R1", ComplianceStatus.SATISFIED)
    result = _make_result([a])

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            _, decisions = run_overlay(result, "contract", budget)

    # SATISFIED is not a target status — no API calls
    assert mock_client.messages.create.call_count == 0
    assert len(decisions) == 0


def test_overlay_handles_unparseable_response(tmp_path):
    budget = BudgetGuard(limit_dkk=200.0, budget_file=tmp_path / "b.json")
    a = _make_assessment("R1", ComplianceStatus.MISSING)
    result = _make_result([a])

    bad_content = MagicMock()
    bad_content.text = "This is not JSON"
    usage = MagicMock()
    usage.input_tokens = 100
    usage.output_tokens = 50
    bad_response = MagicMock()
    bad_response.content = [bad_content]
    bad_response.usage = usage

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.messages.create.return_value = bad_response
            _, decisions = run_overlay(result, "contract", budget)

    assert len(decisions) == 1
    assert decisions[0].revised_status == ComplianceStatus.MISSING
    assert "could not be parsed" in decisions[0].reasoning


def test_overlay_records_tokens_in_decision(tmp_path):
    budget = BudgetGuard(limit_dkk=200.0, budget_file=tmp_path / "b.json")
    a = _make_assessment("R1", ComplianceStatus.MISSING)
    result = _make_result([a])

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.messages.create.return_value = _make_response(confidence=0.9)
            _, decisions = run_overlay(result, "contract", budget)

    assert "input" in decisions[0].tokens_used
    assert "output" in decisions[0].tokens_used


def test_overlay_calls_compute_summary(tmp_path):
    budget = BudgetGuard(limit_dkk=200.0, budget_file=tmp_path / "b.json")
    result = _make_result([])

    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
        with patch("anthropic.Anthropic") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            run_overlay(result, "contract", budget)

    result.compute_summary.assert_called_once()


def test_overlay_decision_dataclass():
    d = OverlayDecision(
        requirement_id="R1",
        original_status=ComplianceStatus.MISSING,
        revised_status=ComplianceStatus.EVIDENCE_FOUND,
        confidence=0.85,
        evidence_text="clause text",
        reasoning="Found relevant language.",
    )
    assert d.requirement_id == "R1"
    assert d.tokens_used == {}
