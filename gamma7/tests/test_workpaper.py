"""
Tests for gamma7.audit_intelligence.workpaper
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from gamma7.audit_intelligence.audit_universe import build_universe, build_program, AuditDecision
from gamma7.audit_intelligence.workpaper import (
    build_workpapers,
    generate_workpaper_markdown,
    generate_summary_report,
    save_workpapers,
    WorkPaperSet,
    WorkPaper,
    Finding,
    TestResult,
    FindingSeverity,
    FindingStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _universe():
    return build_universe(organisation="Test Bank A/S", scope=["iso27001"])


def _wps():
    return build_workpapers(_universe())


# ---------------------------------------------------------------------------
# build_workpapers
# ---------------------------------------------------------------------------

def test_build_workpapers_returns_workpaperset():
    assert isinstance(_wps(), WorkPaperSet)


def test_build_workpapers_count_matches_in_scope():
    u = _universe()
    wps = build_workpapers(u)
    in_scope = sum(1 for c in u.controls if c.audit_decision == AuditDecision.INCLUDE)
    assert len(wps.work_papers) == in_scope


def test_build_workpapers_no_excluded_controls():
    wps = _wps()
    # All work papers should be for included controls only
    assert all(wp.test_result == TestResult.NOT_TESTED for wp in wps.work_papers)


def test_build_workpapers_has_audit_objective():
    wps = _wps()
    for wp in wps.work_papers:
        assert wp.audit_objective, f"{wp.control_id} has no audit objective"


def test_build_workpapers_expected_evidence_populated():
    wps = _wps()
    # Every work paper should have expected evidence from the control definition
    for wp in wps.work_papers:
        assert len(wp.expected_evidence_artifacts) > 0, f"{wp.control_id} has no expected evidence"


def test_build_workpapers_risk_indicators_populated():
    wps = _wps()
    for wp in wps.work_papers:
        assert len(wp.risk_indicators) > 0, f"{wp.control_id} has no risk indicators"


def test_build_workpapers_inherent_risk_set():
    wps = _wps()
    valid_risks = {"CRITICAL", "HIGH", "MEDIUM", "LOW"}
    for wp in wps.work_papers:
        assert wp.inherent_risk in valid_risks


def test_build_workpapers_engagement_id_format():
    wps = _wps()
    assert wps.engagement_id.startswith("WP-")


def test_build_workpapers_metadata_total():
    wps = _wps()
    assert wps.engagement_metadata["total_workpapers"] == len(wps.work_papers)


def test_build_workpapers_get_workpaper_by_id():
    wps = _wps()
    # A.8.2 is in the iso27001 subset
    wp = wps.get_workpaper("A.8.2")
    assert wp is not None
    assert wp.control_id == "A.8.2"


def test_build_workpapers_get_workpaper_missing_returns_none():
    wps = _wps()
    assert wps.get_workpaper("NONEXISTENT") is None


# ---------------------------------------------------------------------------
# WorkPaper.add_finding
# ---------------------------------------------------------------------------

def test_add_finding_creates_finding():
    wps = _wps()
    wp = wps.get_workpaper("A.8.2")
    f = wp.add_finding(
        title="Shared admin credentials in use",
        condition="Three production servers share a single admin account.",
        criteria="ISO 27001 A.8.2 requires individual privileged accounts.",
        cause="No PAM solution implemented; legacy practice not remediated.",
        effect="Inability to attribute privileged actions; breach risk elevated.",
        recommendation="Implement individual named admin accounts within 30 days.",
        severity=FindingSeverity.CRITICAL,
    )
    assert isinstance(f, Finding)
    assert f.severity == FindingSeverity.CRITICAL
    assert f.status == FindingStatus.OPEN
    assert f.finding_id.startswith("WP-")


def test_add_finding_sequential_ids():
    wps = _wps()
    wp = wps.get_workpaper("A.8.5")
    for i in range(3):
        wp.add_finding(
            title=f"Finding {i+1}",
            condition="x", criteria="y", cause="z", effect="e", recommendation="r",
            severity=FindingSeverity.HIGH,
        )
    ids = [f.finding_id for f in wp.findings]
    assert ids[0].endswith("-F01")
    assert ids[1].endswith("-F02")
    assert ids[2].endswith("-F03")


def test_workpaper_has_findings_property():
    wps = _wps()
    wp = wps.get_workpaper("A.8.2")
    assert not wp.has_findings
    wp.add_finding(
        title="x", condition="c", criteria="cr", cause="ca", effect="e",
        recommendation="r", severity=FindingSeverity.LOW,
    )
    assert wp.has_findings


def test_workpaper_open_findings_filter():
    wps = _wps()
    wp = wps.get_workpaper("A.8.2")
    wp.add_finding(
        title="Open", condition="c", criteria="cr", cause="ca", effect="e",
        recommendation="r", severity=FindingSeverity.HIGH,
    )
    wp.add_finding(
        title="Closed", condition="c", criteria="cr", cause="ca", effect="e",
        recommendation="r", severity=FindingSeverity.MEDIUM, status=FindingStatus.CLOSED,
    )
    # Must set status manually after creation for the closed one
    wp.findings[-1].status = FindingStatus.CLOSED
    assert len(wp.open_findings) == 1
    assert wp.open_findings[0].title == "Open"


# ---------------------------------------------------------------------------
# WorkPaperSet computed properties
# ---------------------------------------------------------------------------

def test_wps_tested_count_zero_initially():
    assert _wps().tested_count == 0


def test_wps_all_findings_aggregates_across_workpapers():
    wps = _wps()
    wp1 = wps.get_workpaper("A.8.2")
    wp2 = wps.get_workpaper("A.8.5")
    wp1.add_finding("F1", "c", "cr", "ca", "e", "r", FindingSeverity.CRITICAL)
    wp2.add_finding("F2", "c", "cr", "ca", "e", "r", FindingSeverity.HIGH)
    assert len(wps.all_findings) == 2


def test_wps_findings_by_severity_structure():
    wps = _wps()
    fbs = wps.findings_by_severity
    assert "CRITICAL" in fbs and "HIGH" in fbs and "MEDIUM" in fbs


def test_wps_tested_count_increments():
    wps = _wps()
    wp = wps.work_papers[0]
    wp.test_result = TestResult.PASSED
    assert wps.tested_count == 1
    assert wps.passed_count == 1
    assert wps.exception_count == 0


def test_wps_to_json_roundtrip():
    import json
    wps = _wps()
    data = json.loads(wps.to_json())
    assert data["organisation"] == "Test Bank A/S"
    assert len(data["work_papers"]) > 0


# ---------------------------------------------------------------------------
# generate_workpaper_markdown
# ---------------------------------------------------------------------------

def test_generate_workpaper_markdown_returns_string():
    wps = _wps()
    md = generate_workpaper_markdown(wps.work_papers[0])
    assert isinstance(md, str) and len(md) > 50


def test_generate_workpaper_markdown_contains_control_id():
    wps = _wps()
    wp = wps.get_workpaper("A.8.2")
    md = generate_workpaper_markdown(wp)
    assert "A.8.2" in md


def test_generate_workpaper_markdown_contains_finding():
    wps = _wps()
    wp = wps.get_workpaper("A.8.2")
    wp.add_finding(
        title="Shared admin credentials",
        condition="Shared accounts found.",
        criteria="Individual accounts required.",
        cause="No PAM tool.",
        effect="Attribution impossible.",
        recommendation="Deploy PAM within 30 days.",
        severity=FindingSeverity.CRITICAL,
    )
    wp.test_result = TestResult.EXCEPTION
    md = generate_workpaper_markdown(wp)
    assert "Shared admin credentials" in md
    assert "CRITICAL" in md
    assert "EXCEPTION" in md


def test_generate_workpaper_markdown_no_findings_section_when_empty():
    wps = _wps()
    wp = wps.get_workpaper("A.5.1")
    md = generate_workpaper_markdown(wp)
    assert "## Findings" not in md


# ---------------------------------------------------------------------------
# generate_summary_report
# ---------------------------------------------------------------------------

def test_generate_summary_report_returns_string():
    wps = _wps()
    rpt = generate_summary_report(wps)
    assert isinstance(rpt, str) and len(rpt) > 100


def test_generate_summary_report_contains_org():
    wps = _wps()
    assert "Test Bank A/S" in generate_summary_report(wps)


def test_generate_summary_report_contains_findings_table():
    wps = _wps()
    rpt = generate_summary_report(wps)
    assert "Findings Summary" in rpt
    assert "CRITICAL" in rpt


def test_generate_summary_report_remediation_tracker_with_findings():
    wps = _wps()
    wp = wps.get_workpaper("A.8.8")
    wp.add_finding(
        title="No vuln scanning",
        condition="No scanner configured.",
        criteria="A.8.8 requires periodic vulnerability scanning.",
        cause="Budget not allocated.",
        effect="Undetected vulnerabilities may be exploited.",
        recommendation="Deploy Tenable or similar within 60 days.",
        severity=FindingSeverity.CRITICAL,
        owner="IT Security Manager",
        target_remediation_date="2026-12-31",
    )
    wp.test_result = TestResult.EXCEPTION
    rpt = generate_summary_report(wps)
    assert "Remediation Tracker" in rpt
    assert "IT Security Manager" in rpt
    assert "2026-12-31" in rpt


# ---------------------------------------------------------------------------
# save_workpapers
# ---------------------------------------------------------------------------

def test_save_workpapers_writes_expected_files(tmp_path):
    wps = _wps()
    paths = save_workpapers(wps, tmp_path, include_individual=False)
    assert "summary_md" in paths
    assert "full_json" in paths
    assert "full_yaml" in paths
    for key, path in paths.items():
        assert path.exists(), f"Expected {key} at {path}"


def test_save_workpapers_individual_files(tmp_path):
    wps = _wps()
    paths = save_workpapers(wps, tmp_path, include_individual=True)
    # Should have individual wp_ files
    wp_keys = [k for k in paths if k.startswith("wp_")]
    assert len(wp_keys) == len(wps.work_papers)
    for key in wp_keys:
        assert paths[key].exists()
