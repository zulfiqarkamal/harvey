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
    # ISO 27001 subset has 4 critical controls
    assert len(critical) >= 1
    assert u.metadata["critical"] == len(critical)


def test_build_universe_controls_ranked_by_risk():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    included = [c for c in u.controls if c.audit_decision == AuditDecision.INCLUDE]
    # First included control should have lowest risk order (CRITICAL = 0)
    assert included[0].inherent_risk == InherentRisk.CRITICAL
    # Priority ranks should be sequential starting from 1
    ranks = [c.priority_rank for c in included]
    assert ranks == list(range(1, len(included) + 1))


def test_build_universe_metadata_counts_consistent():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    m = u.metadata
    assert m["total_controls"] == m["in_scope"] + m["excluded"]
    assert m["critical"] + m["high"] + m["medium"] <= m["in_scope"]


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


# --- build_program ---

def test_build_program_returns_audit_program():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    assert isinstance(p, AuditProgram)


def test_build_program_default_four_quarters():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    assert len(p.quarters) == 4
    assert "Q1" in p.quarters and "Q4" in p.quarters


def test_build_program_all_controls_scheduled():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    included = [c.control_id for c in u.controls if c.audit_decision == AuditDecision.INCLUDE]
    scheduled = [cid for ids in p.quarters.values() for cid in ids]
    assert set(scheduled) == set(included)


def test_build_program_no_duplicates_across_quarters():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    all_scheduled = [cid for ids in p.quarters.values() for cid in ids]
    assert len(all_scheduled) == len(set(all_scheduled))


def test_build_program_critical_in_early_quarters():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u, quarters=4)
    q1_q2 = set(p.quarters.get("Q1", []) + p.quarters.get("Q2", []))
    critical_ids = {c.control_id for c in u.controls if c.inherent_risk == InherentRisk.CRITICAL}
    # At least some critical controls should land in Q1 or Q2
    overlap = critical_ids & q1_q2
    assert len(overlap) > 0


def test_build_program_custom_quarter_count():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u, quarters=2)
    assert len(p.quarters) == 2
    assert "Q1" in p.quarters and "Q2" in p.quarters


def test_build_program_planned_quarter_assigned_on_controls():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    build_program(u)
    included = [c for c in u.controls if c.audit_decision == AuditDecision.INCLUDE]
    for ctrl in included:
        assert ctrl.planned_quarter is not None


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
    # At least one control id should appear in the report
    assert "A.8.2" in md or "A.5.1" in md


def test_generate_markdown_schedule_section_with_program():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    p = build_program(u)
    md = generate_markdown(u, p)
    assert "Audit Schedule" in md
    assert "Q1" in md


def test_generate_markdown_no_program_no_schedule():
    u = build_universe(organisation="Test Bank", scope=["iso27001"])
    md = generate_markdown(u, program=None)
    assert "Audit Schedule" not in md


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
