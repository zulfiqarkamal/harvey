"""
Tests for gamma7.audit_intelligence.audit_universe
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from gamma7.audit_intelligence.audit_universe import (
    build_universe,
    build_program,
    generate_markdown,
    AuditUniverse,
    AuditProgram,
    AuditDecision,
    InherentRisk,
    ControlRating,
    ResidualRisk,
    ControlEntry,
)


# --- build_universe ---

def test_build_universe_returns_audit_universe():
    u = build_universe(organisation="Test Bank A/S", scope=["iso27001"])
    assert isinstance(u, AuditUniverse)


def test_build_universe_has_controls():
    u = build_universe(organisation="Test Bank A/S", scope=["iso27001"])
    assert len(u.controls) > 0


def test_build_universe_all_included_by_default():
    u = build_universe(organisation="Test Bank A/S", scope=["iso27001"])
    included = [c for c in u.controls if c.audit_decision == AuditDecision.INCLUDE]
    assert len(included) > 0
    assert u.metadata["in_scope"] == len(included)


def test_build_universe_exclude_reduces_in_scope():
    u_full = build_universe(organisation="Test Bank", scope=["iso27001"])
    u_excl = build_universe(organisation="Test Bank", scope=["iso27001"], exclude_control_ids=["A.7.1", "A.7.8"])
    assert u_excl.metadata["in_scope"] < u_full.metadata["in_scope"]
    assert u_excl.metadata["excluded"] == 2


def test_build_universe_excluded_controls_marked_correctly():
    u = build_universe(organisation="Test Bank", scope=["iso27001"], exclude_control_ids=["A.7.1"])
    excluded = [c for c in u.controls if c.control_id == "A.7.1"]
    assert len(excluded) == 1
    assert excluded[0].audit_decision == AuditDecision.EXCLUDE_OOS


def test_build_universe_has_critical_controls():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    critical = [c for c in u.controls if c.inherent_risk == InherentRisk.CRITICAL]
    assert len(critical) >= 1
    assert u.metadata["critical"] == len(critical)


def test_build_universe_controls_ranked_by_residual_then_inherent():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    included = [c for c in u.controls if c.audit_decision == AuditDecision.INCLUDE]
    # Ranks should be sequential starting from 1
    ranks = [c.priority_rank for c in included]
    assert ranks == list(range(1, len(included) + 1))
    # First included control should have highest risk (residual HIGH or inherent CRITICAL)
    first = included[0]
    assert first.residual_risk in (ResidualRisk.HIGH, ResidualRisk.MEDIUM) or \
           first.inherent_risk in (InherentRisk.CRITICAL, InherentRisk.HIGH)


def test_build_universe_metadata_counts_consistent():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    m = u.metadata
    assert m["total_controls"] == m["in_scope"] + m["excluded"]
    assert m["critical"] + m["high"] + m["medium"] <= m["in_scope"]
    assert m["residual_high"] + m["residual_medium"] + m["residual_low"] == m["in_scope"]


def test_build_universe_to_yaml_roundtrip():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    yaml_str = u.to_yaml()
    assert "organisation: Test Bank" in yaml_str
    assert "iso27001" in yaml_str


def test_build_universe_to_json_roundtrip():
    import json
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    data = json.loads(u.to_json())
    assert data["organisation"] == "Test Bank"
    assert len(data["controls"]) > 0


def test_build_universe_unknown_scope_raises():
    import pytest
    with pytest.raises((ValueError, FileNotFoundError, KeyError)):
        build_universe(organisation="Test Bank", scope=["nonexistent_scope"])


def test_build_universe_all_included_have_residual_risk():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    included = [c for c in u.controls if c.audit_decision == AuditDecision.INCLUDE]
    for ctrl in included:
        assert ctrl.residual_risk is not None
        assert isinstance(ctrl.residual_risk, ResidualRisk)


def test_build_universe_control_ratings_applied():
    ratings = {"A.8.2": "EFFECTIVE", "A.5.1": "WEAK"}
    u = build_universe(organisation="Test Bank", scope=["iso27001"], control_ratings=ratings)
    for ctrl in u.controls:
        if ctrl.control_id == "A.8.2":
            assert ctrl.control_rating == ControlRating.EFFECTIVE
        if ctrl.control_id == "A.5.1":
            assert ctrl.control_rating == ControlRating.WEAK


def test_build_universe_residual_lower_than_or_equal_inherent_for_effective_controls():
    # EFFECTIVE controls should not increase risk
    all_effective = {c: "EFFECTIVE" for c in []}
    u = build_universe(organisation="Test Bank", scope=["iso27001"],
                       control_ratings={c.control_id: "EFFECTIVE"
                                        for c in build_universe("X", ["iso27001"]).controls
                                        if c.audit_decision == AuditDecision.INCLUDE})
    _res_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    _inh_to_res = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 1, "LOW": 2}
    for ctrl in u.controls:
        if ctrl.audit_decision == AuditDecision.INCLUDE and ctrl.residual_risk:
            assert _res_order[ctrl.residual_risk.value] >= _inh_to_res.get(ctrl.inherent_risk.value, 0)


# --- build_program (3-year rolling plan) ---

def test_build_program_returns_audit_program():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    assert isinstance(p, AuditProgram)


def test_build_program_default_three_years():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    assert p.cycle_years == 3
    assert len(p.plan) == 3
    assert "Year 1" in p.plan and "Year 3" in p.plan


def test_build_program_high_residual_in_all_years():
    # Controls with HIGH residual should appear in every year
    u = build_universe(organisation="Test Bank", scope=["iso27001"],
                       control_ratings={c.control_id: "WEAK"
                                        for c in build_universe("X", ["iso27001"]).controls
                                        if c.audit_decision == AuditDecision.INCLUDE
                                        and c.inherent_risk in (InherentRisk.CRITICAL, InherentRisk.HIGH)})
    p = build_program(u)
    high_ids = {c.control_id for c in u.controls
                if c.audit_decision == AuditDecision.INCLUDE and c.residual_risk == ResidualRisk.HIGH}
    if high_ids:
        for year_key in p.plan:
            year_set = set(p.plan[year_key])
            assert len(high_ids & year_set) > 0


def test_build_program_medium_residual_in_odd_years():
    # Controls with MEDIUM residual → Year 1 and Year 3 only
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    medium_ids = {c.control_id for c in u.controls
                  if c.audit_decision == AuditDecision.INCLUDE and c.residual_risk == ResidualRisk.MEDIUM}
    year2_set = set(p.plan.get("Year 2", []))
    # MEDIUM controls must not appear in Year 2
    assert len(medium_ids & year2_set) == 0


def test_build_program_low_residual_key_control_review():
    # LOW residual → review type is key_control_review
    u = build_universe(organisation="Test Bank", scope=["iso27001"],
                       control_ratings={c.control_id: "EFFECTIVE"
                                        for c in build_universe("X", ["iso27001"]).controls
                                        if c.audit_decision == AuditDecision.INCLUDE
                                        and c.inherent_risk == InherentRisk.LOW})
    p = build_program(u)
    for ctrl_id, rtype in p.plan_types.items():
        ctrl = next((c for c in u.controls if c.control_id == ctrl_id), None)
        if ctrl and ctrl.residual_risk == ResidualRisk.LOW:
            assert rtype == "key_control_review"


def test_build_program_planned_quarter_assigned_on_controls():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    build_program(u)
    included = [c for c in u.controls if c.audit_decision == AuditDecision.INCLUDE]
    for ctrl in included:
        assert ctrl.planned_quarter is not None
        assert ctrl.planned_quarter.startswith("Year ")


def test_build_program_no_duplicates_within_a_year():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    for year_key, ctrl_ids in p.plan.items():
        assert len(ctrl_ids) == len(set(ctrl_ids)), f"Duplicates in {year_key}"


def test_build_program_backward_compat_quarters_property():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    # .quarters is a backward-compat alias for .plan
    assert p.quarters is p.plan


# --- generate_markdown ---

def test_generate_markdown_returns_string():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    md = generate_markdown(u, p)
    assert isinstance(md, str)
    assert len(md) > 100


def test_generate_markdown_contains_org_name():
    u = build_universe(organisation="Nordbank A/S", scope=["iso27001"])
    md = generate_markdown(u)
    assert "Nordbank A/S" in md


def test_generate_markdown_contains_control_ids():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    md = generate_markdown(u)
    assert "A.8.2" in md or "A.5.1" in md


def test_generate_markdown_schedule_section_with_program():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    md = generate_markdown(u, p)
    assert "Audit Schedule" in md
    assert "Year 1" in md


def test_generate_markdown_no_program_no_schedule():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    md = generate_markdown(u, program=None)
    assert "Audit Schedule" not in md


def test_generate_markdown_shows_residual_risk():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    md = generate_markdown(u)
    assert "Residual Risk" in md
    assert "Control Rating" in md


# --- save ---

def test_save_writes_expected_files(tmp_path):
    from gamma7.audit_intelligence.audit_universe import save
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    paths = save(u, p, tmp_path)
    assert "universe_yaml" in paths
    assert "universe_json" in paths
    assert "markdown" in paths
    assert "program_yaml" in paths
    for key, path in paths.items():
        assert path.exists(), f"Expected {key} file at {path}"
