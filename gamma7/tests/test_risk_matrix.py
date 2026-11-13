"""
Tests for the 3×3 risk matrix schema types (Agent 4 — Audit Intelligence).

Covers:
- RiskRating enum + RiskScore dataclass (analysis_schema.py)
- ControlRisk enum + residual_score / residual_rating helpers

Risk matrix computation for audit engagements lives in
gamma7.audit_intelligence.risk_heatmap and is tested separately.
"""

from __future__ import annotations

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


