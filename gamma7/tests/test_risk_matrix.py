"""
Tests for the 3×3 risk matrix implementation.

Covers:
- RiskRating enum + RiskScore dataclass (analysis_schema.py)
- ControlRisk enum + residual_score / residual_rating helpers
- compute_risk_heatmap() (analyser.py)
- GET /api/risk-heatmap dashboard endpoint (dashboard_app.py)

No real API calls. All contract analyses use synthetic data.
"""

from __future__ import annotations

import datetime
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from gamma7.schemas.analysis_schema import (
    ControlRisk,
    RiskRating,
    RiskScore,
    residual_rating,
    residual_score,
)
from gamma7.engine.analyser import (
    ComplianceStatus,
    ContractAnalysisResult,
    ContractMetadata,
    RequirementAssessment,
    RiskLevel,
    _control_risk_for_assessment,
    compute_risk_heatmap,
)
from gamma7.dashboard_app import create_app
from gamma7.supplier_assurance.pipeline import PipelineResult
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# RiskScore tests
# ---------------------------------------------------------------------------

class TestRiskScore:
    def test_low_low_is_low(self):
        rs = RiskScore.from_components(1, 1)
        assert rs.score == 1
        assert rs.rating == RiskRating.LOW

    def test_low_high_is_low(self):
        rs = RiskScore.from_components(1, 2)
        assert rs.score == 2
        assert rs.rating == RiskRating.LOW

    def test_medium_medium_is_medium(self):
        rs = RiskScore.from_components(2, 2)
        assert rs.score == 4
        assert rs.rating == RiskRating.MEDIUM

    def test_low_medium_is_medium(self):
        rs = RiskScore.from_components(2, 2)
        assert rs.score == 4
        assert rs.rating == RiskRating.MEDIUM

    def test_medium_high_is_high(self):
        rs = RiskScore.from_components(2, 3)
        assert rs.score == 6
        assert rs.rating == RiskRating.HIGH

    def test_high_high_is_high(self):
        rs = RiskScore.from_components(3, 3)
        assert rs.score == 9
        assert rs.rating == RiskRating.HIGH

    def test_score_boundary_3_is_medium(self):
        rs = RiskScore.from_components(1, 3)
        assert rs.score == 3
        assert rs.rating == RiskRating.MEDIUM

    def test_score_boundary_4_is_medium(self):
        rs = RiskScore.from_components(2, 2)
        assert rs.score == 4
        assert rs.rating == RiskRating.MEDIUM

    def test_score_boundary_6_is_high(self):
        rs = RiskScore.from_components(2, 3)
        assert rs.score == 6
        assert rs.rating == RiskRating.HIGH

    def test_invalid_likelihood_raises(self):
        with pytest.raises(ValueError):
            RiskScore(likelihood=0, impact=2, score=0, rating=RiskRating.LOW)

    def test_invalid_impact_raises(self):
        with pytest.raises(ValueError):
            RiskScore(likelihood=2, impact=4, score=8, rating=RiskRating.HIGH)


# ---------------------------------------------------------------------------
# residual_score / residual_rating tests
# ---------------------------------------------------------------------------

class TestResidualRisk:
    def test_low_control_risk_reduces_score(self):
        inherent = RiskScore.from_components(3, 3)  # score 9
        res = residual_score(inherent, ControlRisk.LOW)
        assert res == pytest.approx(9 * 0.35, abs=0.01)
        assert res == pytest.approx(3.15, abs=0.01)

    def test_medium_control_risk(self):
        inherent = RiskScore.from_components(3, 2)  # score 6
        res = residual_score(inherent, ControlRisk.MEDIUM)
        assert res == pytest.approx(6 * 0.55, abs=0.01)
        assert res == pytest.approx(3.30, abs=0.01)

    def test_high_control_risk_little_reduction(self):
        inherent = RiskScore.from_components(3, 3)  # score 9
        res = residual_score(inherent, ControlRisk.HIGH)
        assert res == pytest.approx(9 * 0.90, abs=0.01)
        assert res == pytest.approx(8.10, abs=0.01)

    def test_residual_rating_low(self):
        assert residual_rating(1.5) == RiskRating.LOW
        assert residual_rating(2.0) == RiskRating.LOW

    def test_residual_rating_medium(self):
        assert residual_rating(2.5) == RiskRating.MEDIUM
        assert residual_rating(4.0) == RiskRating.MEDIUM

    def test_residual_rating_high(self):
        assert residual_rating(4.1) == RiskRating.HIGH
        assert residual_rating(9.0) == RiskRating.HIGH


# ---------------------------------------------------------------------------
# _control_risk_for_assessment tests
# ---------------------------------------------------------------------------

def _assessment(status: ComplianceStatus, risk: RiskLevel = RiskLevel.HIGH) -> RequirementAssessment:
    return RequirementAssessment(
        requirement_id="TEST-001",
        requirement_article="Art. X",
        requirement_summary="Test requirement",
        status=status,
        risk_level=risk,
        assessed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


class TestControlRiskDerivation:
    def test_satisfied_gives_low_control_risk(self):
        a = _assessment(ComplianceStatus.SATISFIED)
        assert _control_risk_for_assessment(a) == "low"

    def test_ambiguous_gives_medium_control_risk(self):
        a = _assessment(ComplianceStatus.AMBIGUOUS)
        assert _control_risk_for_assessment(a) == "medium"

    def test_evidence_found_gives_medium_control_risk(self):
        a = _assessment(ComplianceStatus.EVIDENCE_FOUND)
        assert _control_risk_for_assessment(a) == "medium"

    def test_missing_gives_high_control_risk(self):
        a = _assessment(ComplianceStatus.MISSING)
        assert _control_risk_for_assessment(a) == "high"

    def test_not_assessed_gives_high_control_risk(self):
        a = _assessment(ComplianceStatus.NOT_ASSESSED)
        assert _control_risk_for_assessment(a) == "high"


# ---------------------------------------------------------------------------
# compute_risk_heatmap tests
# ---------------------------------------------------------------------------

def _make_result(assessments: list[RequirementAssessment], contract_id: str = "C-001") -> ContractAnalysisResult:
    result = ContractAnalysisResult(
        analysis_id=f"GA-{contract_id}",
        framework="DORA_ART30",
        contract=ContractMetadata(
            contract_id=contract_id,
            supplier_name="Test Supplier A/S",
            customer_name="Test Bank",
            classification="critical_important",
        ),
        assessments=assessments,
    )
    result.compute_summary()
    return result


class TestComputeRiskHeatmap:
    def test_na_requirements_excluded(self):
        assessments = [
            _assessment(ComplianceStatus.NOT_APPLICABLE),
            _assessment(ComplianceStatus.NOT_APPLICABLE),
        ]
        heatmap = compute_risk_heatmap(_make_result(assessments))
        assert heatmap["requirements"] == []
        assert heatmap["inherent_risk_counts"] == {"low": 0, "medium": 0, "high": 0}

    def test_satisfied_critical_requirement(self):
        a = _assessment(ComplianceStatus.SATISFIED, RiskLevel.CRITICAL)
        heatmap = compute_risk_heatmap(_make_result([a]))
        req = heatmap["requirements"][0]
        assert req["inherent_likelihood"] == 3
        assert req["inherent_impact"] == 3
        assert req["inherent_score"] == 9
        assert req["inherent_rating"] == "high"
        assert req["control_risk"] == "low"
        assert req["residual_score"] < req["inherent_score"]
        assert req["residual_rating"] == "medium"  # 9 * 0.35 = 3.15 → medium

    def test_missing_high_requirement(self):
        a = _assessment(ComplianceStatus.MISSING, RiskLevel.HIGH)
        heatmap = compute_risk_heatmap(_make_result([a]))
        req = heatmap["requirements"][0]
        assert req["inherent_score"] == 6
        assert req["inherent_rating"] == "high"
        assert req["control_risk"] == "high"
        assert req["residual_score"] == pytest.approx(6 * 0.90, abs=0.01)
        assert req["residual_rating"] == "high"

    def test_ambiguous_medium_requirement(self):
        a = _assessment(ComplianceStatus.AMBIGUOUS, RiskLevel.MEDIUM)
        heatmap = compute_risk_heatmap(_make_result([a]))
        req = heatmap["requirements"][0]
        assert req["inherent_score"] == 4
        assert req["inherent_rating"] == "medium"
        assert req["control_risk"] == "medium"

    def test_counts_sum_correctly(self):
        assessments = [
            _assessment(ComplianceStatus.SATISFIED, RiskLevel.CRITICAL),   # high inherent
            _assessment(ComplianceStatus.MISSING, RiskLevel.HIGH),          # high inherent
            _assessment(ComplianceStatus.MISSING, RiskLevel.MEDIUM),        # medium inherent
            _assessment(ComplianceStatus.NOT_APPLICABLE),                   # excluded
        ]
        heatmap = compute_risk_heatmap(_make_result(assessments))
        total = sum(heatmap["inherent_risk_counts"].values())
        assert total == 3  # 3 applicable requirements

    def test_contract_metadata_in_output(self):
        assessments = [_assessment(ComplianceStatus.SATISFIED)]
        heatmap = compute_risk_heatmap(_make_result(assessments, "C-999"))
        assert heatmap["contract_id"] == "C-999"
        assert heatmap["supplier_name"] == "Test Supplier A/S"

    def test_none_risk_level_treated_as_info(self):
        a = RequirementAssessment(
            requirement_id="TEST-002",
            requirement_article="Art. X",
            requirement_summary="Test",
            status=ComplianceStatus.SATISFIED,
            risk_level=None,
            assessed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )
        heatmap = compute_risk_heatmap(_make_result([a]))
        req = heatmap["requirements"][0]
        assert req["inherent_score"] == 1  # INFO → (1,1) → score 1


# ---------------------------------------------------------------------------
# Dashboard /api/risk-heatmap endpoint tests
# ---------------------------------------------------------------------------

def _pipeline_result(
    analysis_id: str = "SA-001",
    supplier_name: str = "Acme A/S",
    assessments: list | None = None,
) -> PipelineResult:
    if assessments is None:
        assessments = [
            _assessment(ComplianceStatus.MISSING, RiskLevel.HIGH),
            _assessment(ComplianceStatus.SATISFIED, RiskLevel.MEDIUM),
        ]
    raw = _make_result(assessments, contract_id=analysis_id)
    return PipelineResult(
        analysis_id=analysis_id,
        contract_path="contract.md",
        supplier_name=supplier_name,
        classification="critical_important",
        overall_status="gaps_found",
        summary=raw.summary_stats,
        output_paths={},
        markdown_report="# Report",
        warnings=[],
        result=raw,
    )


class TestRiskHeatmapEndpoint:
    def test_no_analyses_returns_empty(self):
        client = TestClient(create_app([], [], None))
        resp = client.get("/api/risk-heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert data["contracts"] == []

    def test_single_contract_returns_heatmap(self):
        pr = _pipeline_result()
        client = TestClient(create_app([pr], [], None))
        resp = client.get("/api/risk-heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["contracts"]) == 1
        assert "inherent_risk_counts" in data["contracts"][0]
        assert "residual_risk_counts" in data["contracts"][0]
        assert "requirements" in data["contracts"][0]

    def test_totals_aggregate_across_contracts(self):
        prs = [
            _pipeline_result("SA-001", "Supplier A"),
            _pipeline_result("SA-002", "Supplier B"),
        ]
        client = TestClient(create_app(prs, [], None))
        resp = client.get("/api/risk-heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["contracts"]) == 2
        totals = data["total_inherent_risk_counts"]
        assert sum(totals.values()) > 0

    def test_advisory_notice_present(self):
        pr = _pipeline_result()
        client = TestClient(create_app([pr], [], None))
        data = client.get("/api/risk-heatmap").json()
        assert "advisory" in data
        assert len(data["advisory"]) > 10

    def test_contract_without_result_field_still_returns(self):
        pr = PipelineResult(
            analysis_id="SA-003",
            contract_path="contract.md",
            supplier_name="No-Result Supplier",
            classification="non_critical",
            overall_status="gaps_found",
            summary={},
            output_paths={},
            markdown_report="# Report",
            warnings=[],
            result=None,
        )
        client = TestClient(create_app([pr], [], None))
        resp = client.get("/api/risk-heatmap")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["contracts"]) == 1
        assert data["contracts"][0]["requirements"] == []

    def test_requirement_detail_fields(self):
        pr = _pipeline_result(assessments=[
            _assessment(ComplianceStatus.MISSING, RiskLevel.CRITICAL),
        ])
        client = TestClient(create_app([pr], [], None))
        data = client.get("/api/risk-heatmap").json()
        req = data["contracts"][0]["requirements"][0]
        assert "requirement_id" in req
        assert "inherent_likelihood" in req
        assert "inherent_impact" in req
        assert "inherent_score" in req
        assert "inherent_rating" in req
        assert "control_risk" in req
        assert "residual_score" in req
        assert "residual_rating" in req

    def test_high_inherent_missing_requirement_stays_high_residual(self):
        pr = _pipeline_result(assessments=[
            _assessment(ComplianceStatus.MISSING, RiskLevel.CRITICAL),
        ])
        client = TestClient(create_app([pr], [], None))
        data = client.get("/api/risk-heatmap").json()
        req = data["contracts"][0]["requirements"][0]
        assert req["inherent_rating"] == "high"
        assert req["control_risk"] == "high"
        assert req["residual_rating"] == "high"
