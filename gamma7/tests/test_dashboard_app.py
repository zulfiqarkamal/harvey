"""
Tests for gamma7.dashboard_app

Uses FastAPI TestClient (httpx). No real Uvicorn server is started.
No LLM calls, no real contracts loaded (all synthetic).
"""

import json
import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from gamma7.dashboard_app import app, create_app
from gamma7.engine.register_reconciler import RegisterEntry
from gamma7.supplier_assurance.pipeline import PipelineResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(
    analysis_id="SA-001",
    supplier_name="Acme A/S",
    contract_path="contract.md",
    classification="critical_important",
    overall_status="gaps_found",
    missing=2,
    satisfied=5,
) -> PipelineResult:
    return PipelineResult(
        analysis_id=analysis_id,
        contract_path=contract_path,
        supplier_name=supplier_name,
        classification=classification,
        overall_status=overall_status,
        summary={"missing": missing, "satisfied": satisfied},
        output_paths={},
        markdown_report=f"# Report for {supplier_name}\n\nSome content.",
        warnings=[],
    )


def _entry(
    entry_id="REG-001",
    supplier_name="Acme A/S",
    contract_reference="SA-001",
    classification="critical_important",
) -> RegisterEntry:
    return RegisterEntry(
        entry_id=entry_id,
        supplier_name=supplier_name,
        supplier_country="DK",
        service_type="cloud_hosting",
        service_description="Cloud services",
        contract_reference=contract_reference,
        classification=classification,
    )


@pytest.fixture()
def client_empty():
    """TestClient with no data loaded."""
    create_app([], [], customer_name="Test")
    return TestClient(app)


@pytest.fixture()
def client_full():
    """TestClient with one analysis and one register entry."""
    create_app([_result()], [_entry()], customer_name="TestBank")
    return TestClient(app)


# ---------------------------------------------------------------------------
# /api/health
# ---------------------------------------------------------------------------

def test_health_returns_ok(client_empty):
    r = client_empty.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_health_returns_generated_at(client_empty):
    r = client_empty.get("/api/health")
    assert "generated_at" in r.json()


# ---------------------------------------------------------------------------
# / (HTML dashboard)
# ---------------------------------------------------------------------------

def test_root_returns_html(client_empty):
    r = client_empty.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_root_contains_title(client_empty):
    r = client_empty.get("/")
    assert "GAMMA7" in r.text


def test_root_contains_advisory(client_empty):
    r = client_empty.get("/")
    assert "Human review" in r.text or "human review" in r.text.lower()


# ---------------------------------------------------------------------------
# /api/analyses
# ---------------------------------------------------------------------------

def test_analyses_empty(client_empty):
    r = client_empty.get("/api/analyses")
    assert r.status_code == 200
    assert r.json() == []


def test_analyses_returns_list(client_full):
    r = client_full.get("/api/analyses")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["analysis_id"] == "SA-001"


def test_analyses_has_summary(client_full):
    r = client_full.get("/api/analyses")
    data = r.json()
    assert "summary" in data[0]
    assert data[0]["summary"]["missing"] == 2


def test_analyses_has_classification(client_full):
    r = client_full.get("/api/analyses")
    assert r.json()[0]["classification"] == "critical_important"


# ---------------------------------------------------------------------------
# /api/analyses/{id}
# ---------------------------------------------------------------------------

def test_get_analysis_found(client_full):
    r = client_full.get("/api/analyses/SA-001")
    assert r.status_code == 200
    assert r.json()["analysis_id"] == "SA-001"


def test_get_analysis_not_found(client_full):
    r = client_full.get("/api/analyses/DOES-NOT-EXIST")
    assert r.status_code == 404


def test_get_analysis_has_markdown(client_full):
    r = client_full.get("/api/analyses/SA-001")
    assert "markdown_report" in r.json()
    assert "Acme A/S" in r.json()["markdown_report"]


# ---------------------------------------------------------------------------
# /api/register
# ---------------------------------------------------------------------------

def test_register_empty(client_empty):
    r = client_empty.get("/api/register")
    assert r.status_code == 200
    assert r.json() == []


def test_register_returns_entries(client_full):
    r = client_full.get("/api/register")
    data = r.json()
    assert len(data) == 1
    assert data[0]["entry_id"] == "REG-001"


def test_register_has_classification(client_full):
    r = client_full.get("/api/register")
    assert r.json()[0]["classification"] == "critical_important"


# ---------------------------------------------------------------------------
# /api/reconciliation
# ---------------------------------------------------------------------------

def test_reconciliation_no_register(client_empty):
    r = client_empty.get("/api/reconciliation")
    assert r.status_code == 200
    # Either empty message or a report with zero entries
    data = r.json()
    assert "message" in data or "summary" in data


def test_reconciliation_with_data(client_full):
    r = client_full.get("/api/reconciliation")
    assert r.status_code == 200
    data = r.json()
    assert "summary" in data
    assert data["summary"]["total_register_entries"] == 1
    assert data["summary"]["matched"] == 1


def test_reconciliation_matched_entry(client_full):
    r = client_full.get("/api/reconciliation")
    data = r.json()
    assert len(data["matched"]) == 1
    assert data["matched"][0]["analysis_id"] == "SA-001"


# ---------------------------------------------------------------------------
# /api/reconciliation/markdown
# ---------------------------------------------------------------------------

def test_reconciliation_markdown_no_register(client_empty):
    # With no reconciliation, returns 404
    r = client_empty.get("/api/reconciliation/markdown")
    assert r.status_code == 404


def test_reconciliation_markdown_with_data(client_full):
    r = client_full.get("/api/reconciliation/markdown")
    assert r.status_code == 200
    data = r.json()
    assert "markdown" in data
    assert "Acme A/S" in data["markdown"] or "TestBank" in data["markdown"]


# ---------------------------------------------------------------------------
# Multiple analyses
# ---------------------------------------------------------------------------

def test_multiple_analyses_loaded():
    results = [
        _result("SA-001", "Acme A/S", contract_path="c1.md"),
        _result("SA-002", "Beta BV", contract_path="c2.md", overall_status="compliant"),
    ]
    entries = [
        _entry("REG-001", "Acme A/S", "SA-001"),
        _entry("REG-002", "Beta BV", "SA-002", classification="non_critical"),
    ]
    create_app(results, entries, customer_name="MultiBank")
    client = TestClient(app)

    r = client.get("/api/analyses")
    assert len(r.json()) == 2

    r = client.get("/api/register")
    assert len(r.json()) == 2

    r = client.get("/api/reconciliation")
    assert r.json()["summary"]["matched"] == 2
    assert r.json()["summary"]["register_only"] == 0
    assert r.json()["summary"]["analysis_only"] == 0


def test_reconciliation_analysis_only():
    """Analysis with no matching register entry appears in analysis_only."""
    results = [_result("SA-999", "Orphan Corp", contract_path="orphan.md")]
    entries = [_entry("REG-001", "Acme A/S", "SA-001")]
    create_app(results, entries)
    client = TestClient(app)

    r = client.get("/api/reconciliation")
    data = r.json()
    assert data["summary"]["register_only"] == 1
    assert data["summary"]["analysis_only"] == 1


# ---------------------------------------------------------------------------
# /api/workpapers — no data loaded
# ---------------------------------------------------------------------------

@pytest.fixture()
def client_no_wp():
    """TestClient with no working papers loaded."""
    create_app([], [], customer_name="Test", work_paper_set=None)
    return TestClient(app)


@pytest.fixture()
def client_with_wp():
    """TestClient with a real WorkPaperSet (iso27001 scope, Test Bank A/S)."""
    from gamma7.audit_intelligence.audit_universe import build_universe
    from gamma7.audit_intelligence.workpaper import build_workpapers, TestResult, FindingSeverity
    universe = build_universe(organisation="Test Bank A/S", scope=["iso27001"])
    wps = build_workpapers(universe)
    # Add a finding to one work paper so we can test finding-related paths
    wp = wps.get_workpaper("A.8.2")
    wp.test_result = TestResult.EXCEPTION
    wp.add_finding(
        title="No MFA for privileged accounts",
        condition="No MFA enforced.",
        criteria="A.8.2 requires MFA.",
        cause="Policy gap.",
        effect="Elevated risk.",
        recommendation="Enforce MFA within 30 days.",
        severity=FindingSeverity.HIGH,
        owner="CISO",
        target_remediation_date="2027-03-31",
    )
    create_app([], [], customer_name="Test", work_paper_set=wps)
    return TestClient(app)


def test_workpapers_no_data(client_no_wp):
    r = client_no_wp.get("/api/workpapers")
    assert r.status_code == 200
    assert "message" in r.json()


def test_workpapers_report_no_data(client_no_wp):
    r = client_no_wp.get("/api/workpapers/report")
    assert r.status_code == 404


def test_workpaper_detail_no_data(client_no_wp):
    r = client_no_wp.get("/api/workpapers/A.8.2")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# /api/workpapers — with data
# ---------------------------------------------------------------------------

def test_workpapers_returns_summary(client_with_wp):
    r = client_with_wp.get("/api/workpapers")
    assert r.status_code == 200
    data = r.json()
    assert "engagement_id" in data
    assert "organisation" in data
    assert data["organisation"] == "Test Bank A/S"


def test_workpapers_returns_work_papers_list(client_with_wp):
    r = client_with_wp.get("/api/workpapers")
    data = r.json()
    assert "work_papers" in data
    assert len(data["work_papers"]) > 0


def test_workpapers_work_paper_has_required_fields(client_with_wp):
    r = client_with_wp.get("/api/workpapers")
    wp = r.json()["work_papers"][0]
    for field in ["control_id", "control_title", "inherent_risk", "test_result", "finding_count"]:
        assert field in wp, f"Missing field: {field}"


def test_workpapers_total_controls_matches_list(client_with_wp):
    r = client_with_wp.get("/api/workpapers")
    data = r.json()
    assert data["total_controls"] == len(data["work_papers"])


def test_workpapers_exception_count_reflects_finding(client_with_wp):
    r = client_with_wp.get("/api/workpapers")
    data = r.json()
    assert data["exception_count"] >= 1
    assert data["total_findings"] >= 1


# ---------------------------------------------------------------------------
# /api/workpapers/report
# ---------------------------------------------------------------------------

def test_workpapers_report_returns_markdown(client_with_wp):
    r = client_with_wp.get("/api/workpapers/report")
    assert r.status_code == 200
    data = r.json()
    assert "markdown" in data
    assert "Test Bank A/S" in data["markdown"]


def test_workpapers_report_contains_finding_title(client_with_wp):
    r = client_with_wp.get("/api/workpapers/report")
    assert "No MFA for privileged accounts" in r.json()["markdown"]


def test_workpapers_report_contains_section_headers(client_with_wp):
    r = client_with_wp.get("/api/workpapers/report")
    md = r.json()["markdown"]
    for section in ["Executive Summary", "Findings Detail", "Remediation Action Plan"]:
        assert section in md, f"Missing section: {section}"


# ---------------------------------------------------------------------------
# /api/workpapers/{control_id}
# ---------------------------------------------------------------------------

def test_get_workpaper_detail_found(client_with_wp):
    r = client_with_wp.get("/api/workpapers/A.8.2")
    assert r.status_code == 200
    data = r.json()
    assert data["control_id"] == "A.8.2"


def test_get_workpaper_detail_not_found(client_with_wp):
    r = client_with_wp.get("/api/workpapers/Z.99.99")
    assert r.status_code == 404


def test_get_workpaper_detail_has_findings(client_with_wp):
    r = client_with_wp.get("/api/workpapers/A.8.2")
    data = r.json()
    assert "findings" in data
    assert len(data["findings"]) == 1
    f = data["findings"][0]
    assert f["title"] == "No MFA for privileged accounts"
    assert f["severity"].upper() == "HIGH"
    assert f["owner"] == "CISO"


def test_get_workpaper_detail_has_audit_fields(client_with_wp):
    r = client_with_wp.get("/api/workpapers/A.8.2")
    data = r.json()
    for field in ["audit_objective", "expected_evidence_artifacts", "risk_indicators",
                  "test_result", "workpaper_id", "test_steps_performed", "evidence_gathered"]:
        assert field in data, f"Missing field: {field}"


def test_get_workpaper_no_findings_has_empty_list(client_with_wp):
    # A.8.5 has no findings in this fixture
    r = client_with_wp.get("/api/workpapers/A.8.5")
    assert r.status_code == 200
    assert r.json()["findings"] == []
