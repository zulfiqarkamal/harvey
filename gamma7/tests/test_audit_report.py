"""
Tests for gamma7.audit_intelligence.audit_report
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from gamma7.audit_intelligence.audit_universe import build_universe
from gamma7.audit_intelligence.workpaper import (
    build_workpapers,
    FindingSeverity,
    FindingStatus,
    TestResult,
)
from gamma7.audit_intelligence.audit_report import generate_audit_report, save_audit_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _universe():
    return build_universe(organisation="Test Bank A/S", scope=["iso27001"])


def _empty_wps():
    return build_workpapers(_universe())


def _wps_with_findings():
    wps = build_workpapers(_universe())
    wp = wps.get_workpaper("A.8.2")
    wp.test_result = TestResult.EXCEPTION
    wp.add_finding(
        title="No MFA enforced for privileged accounts",
        condition="Privileged accounts do not require MFA.",
        criteria="ISO 27001 A.8.2 requires privileged access controls including MFA.",
        cause="MFA policy not configured in IAM tooling.",
        effect="Credential theft risk elevated for privileged accounts.",
        recommendation="Enforce MFA for all privileged accounts within 30 days.",
        severity=FindingSeverity.HIGH,
        owner="IT Security Manager",
        target_remediation_date="2027-01-31",
    )
    wp2 = wps.get_workpaper("A.8.5")
    wp2.test_result = TestResult.PASSED
    return wps


def _wps_with_critical():
    wps = build_workpapers(_universe())
    wp = wps.get_workpaper("A.8.8")
    wp.test_result = TestResult.EXCEPTION
    for _ in range(3):
        wp.add_finding(
            title="Critical finding",
            condition="c", criteria="cr", cause="ca", effect="e",
            recommendation="r",
            severity=FindingSeverity.CRITICAL,
        )
    return wps


# ---------------------------------------------------------------------------
# generate_audit_report — structure
# ---------------------------------------------------------------------------

def test_report_returns_string():
    wps = _empty_wps()
    report = generate_audit_report(wps)
    assert isinstance(report, str) and len(report) > 200


def test_report_contains_organisation():
    wps = _empty_wps()
    report = generate_audit_report(wps)
    assert "Test Bank A/S" in report


def test_report_contains_engagement_id():
    wps = _empty_wps()
    report = generate_audit_report(wps)
    assert wps.engagement_id in report


def test_report_contains_all_section_headers():
    wps = _empty_wps()
    report = generate_audit_report(wps)
    for section in ["Executive Summary", "Scope and Objectives", "Methodology",
                    "Control Testing Results", "Findings Detail",
                    "Remediation Action Plan", "Appendix A", "Appendix B"]:
        assert section in report, f"Section missing: {section}"


def test_report_contains_standards_reference():
    wps = _empty_wps()
    report = generate_audit_report(wps, standards_reference="ISO 27001:2022 Annex A")
    assert "ISO 27001:2022 Annex A" in report


def test_report_custom_standards_reference():
    wps = _empty_wps()
    report = generate_audit_report(wps, standards_reference="DORA Art. 30")
    assert "DORA Art. 30" in report


def test_report_contains_auditor_name():
    wps = _empty_wps()
    report = generate_audit_report(wps, auditor_name="Zulfiqar Kamal")
    assert "Zulfiqar Kamal" in report


def test_report_contains_control_testing_table():
    wps = _empty_wps()
    report = generate_audit_report(wps)
    assert "A.8.2" in report
    assert "A.8.5" in report


def test_report_appendix_a_all_controls():
    wps = _empty_wps()
    report = generate_audit_report(wps)
    for wp in wps.work_papers:
        assert wp.control_id in report


# ---------------------------------------------------------------------------
# Overall conclusion — logic
# ---------------------------------------------------------------------------

def test_report_clean_conclusion_no_findings():
    wps = _empty_wps()
    report = generate_audit_report(wps)
    assert "operating effectively" in report or "No significant" in report


def test_report_qualified_conclusion_with_high_finding():
    wps = _wps_with_findings()
    report = generate_audit_report(wps)
    assert "require management attention" in report or "deficiencies" in report


def test_report_adverse_conclusion_with_critical():
    wps = _wps_with_critical()
    report = generate_audit_report(wps)
    assert "material weaknesses" in report or "immediate" in report


# ---------------------------------------------------------------------------
# Findings detail
# ---------------------------------------------------------------------------

def test_report_contains_finding_title():
    wps = _wps_with_findings()
    report = generate_audit_report(wps)
    assert "No MFA enforced for privileged accounts" in report


def test_report_contains_five_c_elements():
    wps = _wps_with_findings()
    report = generate_audit_report(wps)
    for label in ["Condition", "Criteria", "Cause", "Effect", "Recommendation"]:
        assert label in report


def test_report_contains_finding_severity():
    wps = _wps_with_findings()
    report = generate_audit_report(wps)
    assert "HIGH" in report


def test_report_contains_finding_owner():
    wps = _wps_with_findings()
    report = generate_audit_report(wps)
    assert "IT Security Manager" in report


def test_report_contains_target_date():
    wps = _wps_with_findings()
    report = generate_audit_report(wps)
    assert "2027-01-31" in report


def test_report_no_findings_section_says_none():
    wps = _empty_wps()
    report = generate_audit_report(wps)
    assert "No findings were identified" in report


# ---------------------------------------------------------------------------
# Remediation action plan
# ---------------------------------------------------------------------------

def test_remediation_plan_contains_finding():
    wps = _wps_with_findings()
    report = generate_audit_report(wps)
    assert "Remediation Action Plan" in report
    assert "No MFA enforced" in report


def test_remediation_plan_no_findings():
    wps = _empty_wps()
    report = generate_audit_report(wps)
    assert "No remediation actions required" in report


# ---------------------------------------------------------------------------
# Key statistics
# ---------------------------------------------------------------------------

def test_report_statistics_total_controls():
    wps = _empty_wps()
    report = generate_audit_report(wps)
    assert str(len(wps.work_papers)) in report


def test_report_statistics_tested_count():
    wps = _wps_with_findings()
    report = generate_audit_report(wps)
    # 2 controls tested (EXCEPTION + PASSED)
    assert "2" in report


# ---------------------------------------------------------------------------
# Custom parameters
# ---------------------------------------------------------------------------

def test_report_custom_report_date():
    wps = _empty_wps()
    report = generate_audit_report(wps, report_date="01 January 2027")
    assert "01 January 2027" in report


def test_report_custom_engagement_period():
    wps = _empty_wps()
    report = generate_audit_report(wps, engagement_period="Q4 2026")
    assert "Q4 2026" in report


def test_report_custom_confidentiality_notice():
    wps = _empty_wps()
    report = generate_audit_report(wps, confidentiality_notice="STRICTLY CONFIDENTIAL")
    assert "STRICTLY CONFIDENTIAL" in report


# ---------------------------------------------------------------------------
# save_audit_report
# ---------------------------------------------------------------------------

def test_save_audit_report_creates_file(tmp_path):
    wps = _empty_wps()
    path = save_audit_report(wps, tmp_path)
    assert path.exists()
    assert path.suffix == ".md"
    assert path.stat().st_size > 500


def test_save_audit_report_filename_contains_engagement_id(tmp_path):
    wps = _empty_wps()
    path = save_audit_report(wps, tmp_path)
    assert wps.engagement_id in path.name


def test_save_audit_report_creates_output_dir(tmp_path):
    wps = _empty_wps()
    target = tmp_path / "reports" / "audit"
    save_audit_report(wps, target)
    assert target.exists()
