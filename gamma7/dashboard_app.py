"""
Gamma7 — Unified Dashboard

FastAPI app serving a minimal HTML dashboard showing:
  - Supplier Assurance: analysis results for assessed contracts, ICT register,
    reconciliation (Agent 2)
  - Audit Intelligence: working papers per control, findings, remediation
    tracker, downloadable audit report (Agent 4)

Usage:
    python -m gamma7.dashboard_app --results-dir output/ [--register register.yaml]
    python -m gamma7.dashboard_app --workpapers output/workpapers.json
    python -m gamma7.dashboard_app --demo   # loads synthetic contracts

IMPORTANT: This dashboard is for internal review only.
All findings require human review before regulatory reporting.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import json
import os
import pathlib
import secrets
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response as _PlainResponse

from .engine.analyser import ContractAnalyser, ContractMetadata
from .engine.document_loader import load as load_document
from .engine.register_reconciler import (
    ReconciliationReport,
    RegisterEntry,
    load_register,
    reconcile,
)
from .supplier_assurance.pipeline import PipelineResult, SupplierAssurancePipeline
from .audit_intelligence.workpaper import WorkPaperSet


# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Gamma7 Dashboard",
    description="Supplier Assurance + Audit Intelligence — advisory only",
    version="0.2.0",
)

_pipeline_results: list[PipelineResult] = []
_register: list[RegisterEntry] = []
_reconciliation: Optional[ReconciliationReport] = None
_work_paper_set: Optional[WorkPaperSet] = None


# ---------------------------------------------------------------------------
# HTTP Basic Auth middleware
# Enabled when DEMO_PASSWORD env var is set; skipped for local dev.
# ---------------------------------------------------------------------------

class _BasicAuthMiddleware(BaseHTTPMiddleware):
    """Require HTTP Basic Auth (username: gamma7, password: DEMO_PASSWORD)."""

    async def dispatch(self, request: Request, call_next):
        password = os.environ.get("DEMO_PASSWORD", "")
        if not password:
            # Auth disabled — allow all (local dev mode)
            return await call_next(request)
        if request.url.path == "/api/health":
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                username, _, given_password = decoded.partition(":")
                if username == "gamma7" and secrets.compare_digest(given_password, password):
                    return await call_next(request)
            except Exception:
                pass
        return _PlainResponse(
            content="Authentication required",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="Gamma7 Demo"'},
        )


app.add_middleware(_BasicAuthMiddleware)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}


@app.get("/api/analyses")
def list_analyses():
    """Return summary of all loaded contract analyses."""
    return [
        {
            "analysis_id": r.analysis_id,
            "contract_key": pathlib.Path(r.contract_path).stem,
            "contract_path": r.contract_path,
            "supplier_name": r.supplier_name,
            "classification": r.classification,
            "overall_status": r.overall_status,
            "summary": r.summary,
            "warnings": r.warnings,
        }
        for r in _pipeline_results
    ]


@app.get("/api/analyses/{key}")
def get_analysis(key: str):
    """Return full analysis detail. `key` may be analysis_id or contract filename stem."""
    for r in _pipeline_results:
        if r.analysis_id == key or pathlib.Path(r.contract_path).stem == key:
            return {
                "analysis_id": r.analysis_id,
                "contract_key": pathlib.Path(r.contract_path).stem,
                "contract_path": r.contract_path,
                "supplier_name": r.supplier_name,
                "classification": r.classification,
                "overall_status": r.overall_status,
                "summary": r.summary,
                "markdown_report": r.markdown_report,
                "warnings": r.warnings,
                "overlay_summary": r.overlay_summary,
            }
    raise HTTPException(status_code=404, detail=f"Analysis '{key}' not found")


@app.get("/api/analyses/{key}/assessments")
def get_analysis_assessments(key: str):
    """Return structured per-requirement assessments for a contract analysis."""
    for r in _pipeline_results:
        if r.analysis_id == key or pathlib.Path(r.contract_path).stem == key:
            return {"assessments": getattr(r, "assessments", [])}
    raise HTTPException(status_code=404, detail=f"Analysis '{key}' not found")


@app.post("/api/analyse")
async def analyse_upload(file: UploadFile = File(...)):
    """Accept a contract file upload, run the pipeline, return result JSON, and add to in-memory state."""
    import tempfile, os
    content = await file.read()
    suffix = pathlib.Path(file.filename or "contract.md").suffix or ".md"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        pipeline = SupplierAssurancePipeline()
        result = pipeline.run(
            contract_path=tmp_path,
            supplier_name=file.filename or "Uploaded Supplier",
            customer_name="Demo Customer",
            classification="critical_important",
        )
        _pipeline_results.append(result)
        return {
            "analysis_id": result.analysis_id,
            "contract_key": pathlib.Path(result.contract_path).stem,
            "supplier_name": result.supplier_name,
            "classification": result.classification,
            "overall_status": result.overall_status,
            "summary": result.summary,
            "markdown_report": result.markdown_report,
            "warnings": result.warnings,
        }
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@app.get("/api/register")
def list_register():
    """Return all ICT register entries."""
    return [
        {
            "entry_id": e.entry_id,
            "supplier_name": e.supplier_name,
            "supplier_country": e.supplier_country,
            "service_type": e.service_type,
            "contract_reference": e.contract_reference,
            "classification": e.classification,
            "exit_strategy_documented": e.exit_strategy_documented,
        }
        for e in _register
    ]


@app.get("/api/reconciliation")
def get_reconciliation():
    """Return reconciliation report (register ↔ contract analyses)."""
    if _reconciliation is None:
        return {"message": "No reconciliation available. Load a register first."}
    return json.loads(_reconciliation.to_json())


@app.get("/api/reconciliation/markdown")
def get_reconciliation_markdown():
    """Return reconciliation report as Markdown text."""
    if _reconciliation is None:
        raise HTTPException(status_code=404, detail="No reconciliation available")
    return {"markdown": _reconciliation.to_markdown()}


# ---------------------------------------------------------------------------
# Working papers endpoints (Agent 4 — Audit Intelligence)
# ---------------------------------------------------------------------------

@app.get("/api/workpapers")
def list_workpapers():
    """Return WorkPaperSet summary and per-control status list."""
    if _work_paper_set is None:
        return {"message": "No working papers loaded."}
    wps = _work_paper_set
    papers = []
    for wp in sorted(wps.work_papers, key=lambda w: w.control_id):
        papers.append({
            "control_id": wp.control_id,
            "control_title": wp.control_title,
            "control_domain": wp.control_domain,
            "inherent_risk": wp.inherent_risk,
            "test_result": wp.test_result.value,
            "finding_count": len(wp.findings),
            "open_finding_count": len(wp.open_findings),
        })
    return {
        "engagement_id": wps.engagement_id,
        "organisation": wps.organisation,
        "scope": wps.scope,
        "total_controls": len(wps.work_papers),
        "tested_count": wps.tested_count,
        "passed_count": wps.passed_count,
        "exception_count": wps.exception_count,
        "total_findings": len(wps.all_findings),
        "open_findings": len([f for f in wps.all_findings if f.status.value == "open"]),
        "work_papers": papers,
    }


@app.get("/api/workpapers/report")
def get_workpapers_report():
    """Return formal audit report as Markdown text."""
    if _work_paper_set is None:
        raise HTTPException(status_code=404, detail="No working papers loaded")
    from .audit_intelligence.audit_report import generate_audit_report
    return {"markdown": generate_audit_report(_work_paper_set)}


@app.get("/api/workpapers/{control_id}")
def get_workpaper(control_id: str):
    """Return single work paper detail including findings."""
    if _work_paper_set is None:
        raise HTTPException(status_code=404, detail="No working papers loaded")
    wp = _work_paper_set.get_workpaper(control_id)
    if wp is None:
        raise HTTPException(status_code=404, detail=f"Work paper '{control_id}' not found")
    findings = []
    for f in wp.findings:
        findings.append({
            "finding_id": f.finding_id,
            "title": f.title,
            "severity": f.severity.value,
            "status": f.status.value,
            "condition": f.condition,
            "criteria": f.criteria,
            "cause": f.cause,
            "effect": f.effect,
            "recommendation": f.recommendation,
            "owner": f.owner,
            "target_remediation_date": f.target_remediation_date,
        })
    return {
        "control_id": wp.control_id,
        "control_title": wp.control_title,
        "control_domain": wp.control_domain,
        "inherent_risk": wp.inherent_risk,
        "audit_objective": wp.audit_objective,
        "expected_evidence_artifacts": wp.expected_evidence_artifacts,
        "risk_indicators": wp.risk_indicators,
        "test_result": wp.test_result.value,
        "test_steps_performed": wp.test_steps_performed,
        "evidence_gathered": wp.evidence_gathered,
        "auditor_notes": wp.auditor_notes,
        "findings": findings,
        "workpaper_id": wp.workpaper_id,
    }



@app.get("/api/risk-heatmap")
def get_risk_heatmap():
    """Return InherentRisk × TestResult control count matrix for the heatmap widget."""
    if _work_paper_set is None:
        return {"message": "No working papers loaded."}
    risk_levels = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    test_results = ["PASSED", "EXCEPTION", "NOT_TESTED", "NOT_APPLICABLE"]
    matrix: dict[str, dict[str, int]] = {r: {t: 0 for t in test_results} for r in risk_levels}
    for wp in _work_paper_set.work_papers:
        r = wp.inherent_risk
        t = wp.test_result.value
        if r in matrix and t in matrix[r]:
            matrix[r][t] += 1
    return {
        "risk_levels": risk_levels,
        "test_results": test_results,
        "matrix": matrix,
        "total": len(_work_paper_set.work_papers),
    }


@app.get("/", response_class=HTMLResponse)
def dashboard():
    """Serve the main dashboard HTML."""
    return HTMLResponse(content=_DASHBOARD_HTML)


# ---------------------------------------------------------------------------
# Dashboard HTML (self-contained, no build step)
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gamma7 — Supplier Assurance</title>
<style>
  :root {
    --bg: #0f1117;
    --surface: #1a1d2e;
    --surface2: #242740;
    --border: #2e3155;
    --text: #e2e8f0;
    --muted: #8892b0;
    --accent: #7c6aff;
    --green: #4ade80;
    --yellow: #fbbf24;
    --red: #f87171;
    --orange: #fb923c;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    font-size: 14px;
    line-height: 1.5;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); min-height: 100vh; }

  header {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    padding: 0 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 56px;
  }
  .logo { font-weight: 700; font-size: 16px; letter-spacing: 0.05em; color: var(--accent); }
  .logo span { color: var(--muted); font-weight: 400; }
  .advisory { background: #2d1b00; color: var(--orange); border: 1px solid #7c3c00;
    padding: 4px 10px; border-radius: 4px; font-size: 12px; }

  nav {
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    display: flex;
    padding: 0 24px;
    gap: 0;
  }
  nav button {
    background: none; border: none; color: var(--muted); padding: 12px 16px;
    cursor: pointer; font-size: 13px; border-bottom: 2px solid transparent;
    transition: all 0.15s;
  }
  nav button.active { color: var(--text); border-bottom-color: var(--accent); }
  nav button:hover { color: var(--text); }

  main { padding: 24px; max-width: 1200px; margin: 0 auto; }

  .tab-pane { display: none; }
  .tab-pane.active { display: block; }

  .summary-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 8px; padding: 16px;
  }
  .card .label { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
  .card .value { font-size: 28px; font-weight: 700; margin-top: 4px; }
  .card .value.green { color: var(--green); }
  .card .value.yellow { color: var(--yellow); }
  .card .value.red { color: var(--red); }

  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 10px 12px; border-bottom: 2px solid var(--border);
    color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }
  td { padding: 10px 12px; border-bottom: 1px solid var(--border); vertical-align: top; }
  tr:hover td { background: var(--surface2); cursor: pointer; }
  .table-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }

  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 999px;
    font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.04em;
  }
  .badge-critical { background: #3b1515; color: var(--red); }
  .badge-non-critical { background: #1a2a1a; color: var(--green); }
  .badge-gaps { background: #2d1b00; color: var(--orange); }
  .badge-compliant { background: #0d2a0d; color: var(--green); }
  .badge-matched { background: #1a1a3b; color: #a5b4fc; }
  .badge-missing { background: #2d1b00; color: var(--orange); }

  .detail-panel {
    display: none; background: var(--surface2); border: 1px solid var(--border);
    border-radius: 8px; padding: 20px; margin-top: 16px;
  }
  .detail-panel.open { display: block; }
  .detail-panel h3 { margin-bottom: 12px; font-size: 15px; }
  .detail-panel pre {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 14px; overflow-x: auto; font-size: 12px; color: var(--muted); white-space: pre-wrap;
  }

  .section-title { font-size: 15px; font-weight: 600; margin-bottom: 12px; }
  .empty { color: var(--muted); padding: 24px; text-align: center; }
  .recon-section { margin-bottom: 24px; }
  .loading { color: var(--muted); padding: 40px; text-align: center; }
  .risk-critical { color: var(--red); font-weight: 600; }
  .risk-high { color: var(--orange); font-weight: 600; }
  .risk-medium { color: var(--yellow); }
  .risk-low { color: var(--green); }
  .result-exception { color: var(--orange); }
  .result-passed { color: var(--green); }
  .result-not_tested { color: var(--muted); }
  .finding-row { margin-bottom: 16px; padding: 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; }
  .finding-row h4 { margin-bottom: 8px; font-size: 13px; }
  .finding-meta { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
  .five-c { font-size: 12px; color: var(--muted); line-height: 1.6; }
  .five-c strong { color: var(--text); }
  .btn { background: var(--accent); color: white; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; text-decoration: none; display: inline-block; }
  .btn:hover { opacity: 0.85; }

  /* Tooltip */
  .tooltip-wrap { position: relative; display: inline-flex; align-items: center; gap: 4px; cursor: default; }
  .tooltip-icon { color: var(--muted); font-size: 11px; cursor: help; }
  .tooltip-box {
    display: none; position: absolute; top: 100%; left: 50%; transform: translateX(-50%);
    background: #2a2d45; border: 1px solid var(--border); border-radius: 6px;
    padding: 8px 12px; font-size: 12px; color: var(--text); white-space: normal;
    width: 220px; z-index: 100; line-height: 1.5; margin-top: 4px;
    box-shadow: 0 4px 16px rgba(0,0,0,0.4); font-weight: 400; text-transform: none; letter-spacing: 0;
  }
  .tooltip-wrap:hover .tooltip-box { display: block; }

  /* Control strength badges */
  .ctrl-effective { background: #0d2a0d; color: var(--green); }
  .ctrl-partial   { background: #2d2200; color: var(--yellow); }
  .ctrl-weak      { background: #2d1515; color: var(--red); }

  /* Residual risk badges */
  .res-high   { background: #3b1515; color: var(--red); font-weight: 700; }
  .res-medium { background: #2d1b00; color: var(--orange); }
  .res-low    { background: #0d2a0d; color: var(--green); }

  /* Tab content header */
  .tab-header { margin-bottom: 16px; }
  .tab-header h2 { font-size: 15px; font-weight: 600; margin-bottom: 4px; }
  .tab-header p  { font-size: 12px; color: var(--muted); line-height: 1.6; }

  /* Findings table inside detail panel */
  .findings-table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
  .findings-table th { text-align: left; padding: 8px 10px; border-bottom: 2px solid var(--border); color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }
  .findings-table td { padding: 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
  .findings-table tr:last-child td { border-bottom: none; }
  .status-missing   { color: var(--red); }
  .status-ambiguous { color: var(--yellow); }
  .status-satisfied { color: var(--green); }
  .status-na        { color: var(--muted); }

  /* Modal overlay */
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.65); z-index: 200; overflow-y: auto; padding: 24px; align-items: flex-start; justify-content: center; }
  .modal-overlay.open { display: flex; }
  .modal-box { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 24px; max-width: 820px; width: 100%; position: relative; margin: auto; }
  .modal-close { position: absolute; top: 12px; right: 16px; cursor: pointer; color: var(--muted); font-size: 20px; border: none; background: none; line-height: 1; }
  .modal-close:hover { color: var(--text); }

  /* Legislation banners */
  .leg-banner { display: inline-block; padding: 6px 14px 6px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; margin-bottom: 14px; margin-right: 6px; }
  .leg-dora { background: #1a1a3b; color: #a5b4fc; border-left: 3px solid #7c6aff; }
  .leg-gdpr { background: #1a2d1a; color: #86efac; border-left: 3px solid #4ade80; }

  /* Consequence column */
  .consequence-cell { font-size: 11px; color: var(--orange); font-style: italic; line-height: 1.4; max-width: 180px; }

  /* Board Report */
  .board-section { margin-bottom: 28px; }
  .board-section-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 8px; }
  .finding-compact { padding: 12px 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; margin-bottom: 8px; }
  .finding-compact-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
  .audit-report-row { padding: 10px 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; margin-bottom: 6px; display: flex; justify-content: space-between; align-items: center; gap: 12px; }

  /* Working paper text */
  .wp-text-block { font-size: 13px; line-height: 1.8; padding: 14px 16px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); margin-bottom: 14px; }
  .wp-declaration-label { font-size: 11px; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); margin-bottom: 6px; }
</style>
</head>
<body>

<header>
  <div class="logo">GAMMA7 <span>Audit &amp; Compliance</span></div>
  <div class="advisory">&#9888; Advisory — Human review required before regulatory use</div>
</header>

<nav>
  <button class="active" onclick="showTab('overview')">Overview</button>
  <button onclick="showTab('contracts')">Contracts</button>
  <button onclick="showTab('register')">ICT Register</button>
  <button onclick="showTab('reconciliation')">Reconciliation</button>
  <button onclick="showTab('workpapers')">Working Papers</button>
  <button onclick="showTab('audit')">Audit Intelligence</button>
  <button onclick="showTab('boardreport')">Board Report</button>
</nav>

<main>
  <!-- Overview tab -->
  <div id="tab-overview" class="tab-pane active">
    <div class="summary-cards" id="summary-cards">
      <div class="card"><div class="label">Contracts</div><div class="value" id="stat-total">&#8212;</div></div>
      <div class="card"><div class="label"><span class="tooltip-wrap">Gaps Found<span class="tooltip-icon">ⓘ</span><span class="tooltip-box" style="width:240px">Contracts where at least one DORA Art. 30 or GDPR Art. 28 requirement is missing or ambiguous. Click a row in the table below for the specific gaps.</span></span></div><div class="value yellow" id="stat-gaps">&#8212;</div></div>
      <div class="card"><div class="label">Compliant</div><div class="value green" id="stat-compliant">&#8212;</div></div>
      <div class="card"><div class="label"><span class="tooltip-wrap">Register Entries<span class="tooltip-icon">ⓘ</span><span class="tooltip-box" style="width:240px">Total ICT third-party providers in the DORA Art. 28(3) register. May differ from the number of contract analyses if a provider is registered but not yet assessed — see Register Gaps.</span></span></div><div class="value" id="stat-register">&#8212;</div></div>
      <div class="card"><div class="label"><span class="tooltip-wrap">Register Gaps<span class="tooltip-icon">ⓘ</span><span class="tooltip-box" style="width:240px">Register entries with no matching contract analysis. DORA requires all ICT providers to be assessed. See the Reconciliation tab for detail.</span></span></div><div class="value red" id="stat-recon-gaps">&#8212;</div></div>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Supplier</th>
          <th><span class="tooltip-wrap">Classification<span class="tooltip-icon">ⓘ</span><span class="tooltip-box"><strong>Critical/Important:</strong> ICT-funktion er kritisk for finansielle kerneprocesser — strengere DORA Art. 30-krav gælder.<br><br><strong>Non-critical:</strong> Understøttende funktion — færre obligatoriske klausuler.</span></span></th>
          <th>Status</th>
          <th><span class="tooltip-wrap">Missing<span class="tooltip-icon">ⓘ</span><span class="tooltip-box">Antal DORA/GDPR-krav, der ikke er fundet i kontrakten. Klik på rækken under Contracts for at se præcis hvilke krav der mangler og hvad der bør tilføjes.</span></span></th>
          <th>Satisfied</th>
          <th><span class="tooltip-wrap">Control Strength<span class="tooltip-icon">ⓘ</span><span class="tooltip-box"><strong>Effective:</strong> 0 manglende krav.<br><strong>Partially Effective:</strong> 1–5 mangler.<br><strong>Weak Controls:</strong> 6+ mangler.</span></span></th>
          <th><span class="tooltip-wrap">Residual Risk<span class="tooltip-icon">ⓘ</span><span class="tooltip-box">Kombinerer klassifikation og control strength.<br><strong>High:</strong> Kritisk + Weak Controls.<br><strong>Medium:</strong> Kritisk + Partial, eller Non-critical + Weak.<br><strong>Low:</strong> Øvrige.</span></span></th>
        </tr></thead>
        <tbody id="overview-table"><tr><td colspan="7" class="empty">Loading&#8230;</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Contracts tab -->
  <div id="tab-contracts" class="tab-pane">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <div class="section-title" style="margin:0">Contract Analyses</div>
      <label class="btn" style="cursor:pointer">
        ⬆ Upload Contract
        <input type="file" id="upload-input" accept=".md,.txt,.pdf" style="display:none" onchange="uploadContract(this)">
      </label>
    </div>
    <div id="upload-status" style="display:none;margin-bottom:12px;padding:10px 14px;border-radius:6px;font-size:13px"></div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Analysis ID</th><th>Supplier</th><th>Contract</th>
          <th><span class="tooltip-wrap">Classification<span class="tooltip-icon">ⓘ</span><span class="tooltip-box"><strong>Critical/Important:</strong> ICT-funktion er kritisk for finansielle kerneprocesser — strengere DORA Art. 30-krav gælder.<br><br><strong>Non-critical:</strong> Understøttende funktion — færre obligatoriske klausuler.</span></span></th>
          <th>Status</th>
          <th><span class="tooltip-wrap">Missing<span class="tooltip-icon">ⓘ</span><span class="tooltip-box">Antal DORA/GDPR-krav, der ikke er fundet i kontrakten. Klik på rækken for at se præcis hvilke krav der mangler og hvad der bør tilføjes.</span></span></th>
          <th><span class="tooltip-wrap">Control Strength<span class="tooltip-icon">ⓘ</span><span class="tooltip-box"><strong>Effective:</strong> 0 manglende krav.<br><strong>Partially Effective:</strong> 1–5 mangler.<br><strong>Weak Controls:</strong> 6+ mangler.</span></span></th>
          <th><span class="tooltip-wrap">Residual Risk<span class="tooltip-icon">ⓘ</span><span class="tooltip-box">Kombinerer klassifikation og control strength.<br><strong>High:</strong> Kritisk + Weak Controls.<br><strong>Medium:</strong> Kritisk + Partial, eller Non-critical + Weak.<br><strong>Low:</strong> Øvrige.</span></span></th>
        </tr></thead>
        <tbody id="contracts-table"><tr><td colspan="8" class="empty">Loading&#8230;</td></tr></tbody>
      </table>
    </div>
    <div id="contract-detail" class="detail-panel"></div>
  </div>

  <!-- Register tab -->
  <div id="tab-register" class="tab-pane">
    <div class="tab-header">
      <h2>ICT Third-Party Register — DORA Art. 28(3)</h2>
      <p>DORA forpligter finansielle virksomheder til at føre et register over alle ICT-tredjepartsleverandører. Registret skal indeholde oplysninger om servicetype, klassifikation (kritisk/vigtig vs. ikke-kritisk), kontraktreference og dokumentation for exitstrategi. <strong>Reconciliation-fanen</strong> viser, om registerposterne matcher de kontrakter, der er analyseret.</p>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Entry ID</th><th>Supplier</th><th>Country</th><th>Service Type</th>
          <th>Contract Ref</th><th>Classification</th><th>Exit Strategy</th>
        </tr></thead>
        <tbody id="register-table"><tr><td colspan="7" class="empty">Loading&#8230;</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Reconciliation tab -->
  <div id="tab-reconciliation" class="tab-pane">
    <div class="tab-header">
      <h2>ICT Register Reconciliation — DORA Art. 28(3)</h2>
      <p>DORA Art. 28(3) requires financial entities to maintain a complete register of all ICT third-party service providers. This reconciliation checks: (1) Does every entry in the ICT register have a corresponding contract analysis? (2) Are there any analysed contracts not yet registered? <strong>Register only:</strong> provider in the register with no contract analysis — follow-up required. <strong>Contract only:</strong> contract analysed but not yet in the register — must be added.</p>
    </div>
    <div id="recon-content"><div class="loading">Loading reconciliation&#8230;</div></div>
  </div>

  <!-- Working Papers tab (Agent 4) -->
  <div id="tab-workpapers" class="tab-pane">
    <div class="tab-header">
      <h2>Working Papers — Agent 2: Supplier Assurance</h2>
      <p>IIA 5-C revisionsdokumentation: <strong>Condition</strong> (hvad er observeret), <strong>Criteria</strong> (krav / standard), <strong>Cause</strong> (årsag til afvigelse), <strong>Effect</strong> (konsekvens), <strong>Recommendation</strong> (anbefalet handling). Klik på et kontrolmål for at se de fulde findings og testresultater. <strong>Control Strength</strong> beregnes ud fra antal manglende krav; <strong>Residual Risk</strong> kombinerer klassifikation og control strength.</p>
    </div>
    <div id="wp-content"><div class="loading">Loading working papers&#8230;</div></div>
  </div>

  <!-- Audit Intelligence tab (Agent 4) -->
  <div id="tab-audit" class="tab-pane">
    <div class="tab-header">
      <h2>Audit Intelligence — Agent 4: Intern Revisionsplanlægning</h2>
      <p>Agent 4 understøtter intern revision efter ISACA/IIA/COSO: audit universe, risikovurdering, revisionsprogrammer, kontroltest og ISAE 3402-rapportering. 3×3 risk matrix: Likelihood × Impact (1–9). Heatmappen viser iboende risiko × testresultat. <em>Agent 2 (Working Papers) dækker leverandørsikring; Agent 4 dækker den bredere interne revisionsproces.</em></p>
    </div>
    <div id="audit-content"><div class="loading">Loading audit intelligence…</div></div>
  </div>

  <!-- Board Report tab -->
  <div id="tab-boardreport" class="tab-pane">
    <div class="tab-header">
      <h2>Board Report — Audit Findings Summary</h2>
      <p>Aggregated view of audit findings and results for board and senior management reporting. Finalized audit reports, open findings under follow-up, and control status overview. <strong>Human review required</strong> before distributing to governance bodies.</p>
    </div>
    <div id="boardreport-content"><div class="loading">Loading board report&#8230;</div></div>
  </div>
</main>

<!-- Contract summary modal -->
<div id="contract-modal" class="modal-overlay" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="modal-box">
    <button class="modal-close" onclick="document.getElementById('contract-modal').classList.remove('open')">&#10005;</button>
    <div id="contract-modal-content"></div>
  </div>
</div>

<script>
let analyses = [];
let registerEntries = [];

function showTab(name) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  event.target.classList.add('active');
}

function statusBadge(status) {
  if (!status) return '';
  const cls = status.includes('gap') || status.includes('missing') ? 'badge-gaps'
    : status === 'compliant' ? 'badge-compliant' : 'badge-missing';
  return `<span class="badge ${cls}">${status.replace(/_/g, ' ')}</span>`;
}

function classBadge(cls) {
  const c = cls === 'critical_important' ? 'badge-critical' : 'badge-non-critical';
  const label = cls === 'critical_important' ? 'Critical' : 'Non-critical';
  return `<span class="badge ${c}">${label}</span>`;
}

// Control Strength helpers (based on missing count from summary)
function controlStrength(missing) {
  if (missing === 0) return 'Effective';
  if (missing <= 5) return 'Partially Effective';
  return 'Weak Controls';
}
function controlStrengthClass(missing) {
  if (missing === 0) return 'ctrl-effective';
  if (missing <= 5) return 'ctrl-partial';
  return 'ctrl-weak';
}

// Residual Risk helpers (based on classification + missing count)
function residualRisk(classification, missing) {
  const isCritical = classification === 'critical_important';
  const cs = controlStrength(missing);
  if (isCritical && cs === 'Weak Controls') return 'High';
  if ((isCritical && cs === 'Partially Effective') || (!isCritical && cs === 'Weak Controls')) return 'Medium';
  return 'Low';
}
function residualRiskClass(classification, missing) {
  const rr = residualRisk(classification, missing);
  if (rr === 'High') return 'res-high';
  if (rr === 'Medium') return 'res-medium';
  return 'res-low';
}

// Working Papers variants (based on finding counts from workpaper data)
function controlStrengthWP(missing) { return controlStrength(missing); }
function controlStrengthClassWP(missing) { return controlStrengthClass(missing); }
function residualRiskWP(inherentRisk, missing) {
  const isHigh = inherentRisk === 'CRITICAL' || inherentRisk === 'HIGH';
  const cs = controlStrength(missing);
  if (isHigh && cs === 'Weak Controls') return 'High';
  if ((isHigh && cs === 'Partially Effective') || (!isHigh && cs === 'Weak Controls')) return 'Medium';
  return 'Low';
}
function residualRiskClassWP(inherentRisk, missing) {
  const rr = residualRiskWP(inherentRisk, missing);
  if (rr === 'High') return 'res-high';
  if (rr === 'Medium') return 'res-medium';
  return 'res-low';
}

// ── Consequence lookup (client-side, no backend changes needed) ───────────
const CONSEQUENCES = {
  'ART30-001': 'Supplier may sub-contract core ICT functions without customer approval, introducing unvetted third-party risk and a potential DORA Art. 28 breach.',
  'ART30-002': 'Customer data may be processed or stored in jurisdictions without adequate protection, violating GDPR data transfer rules and DORA location transparency.',
  'ART30-003': 'No contractual security baseline — data breaches cannot be attributed to contractual failure, limiting regulatory and legal recourse.',
  'ART30-004': 'No enforceable service level targets — the financial entity cannot hold the supplier accountable for outages affecting critical operations.',
  'ART30-005': 'Supplier not obligated to report incidents, leaving the financial entity unable to meet its own reporting obligations under DORA Art. 19.',
  'ART30-006': 'No right to audit or inspect supplier — the financial entity cannot verify DORA Art. 30 compliance, exposing it to supervisory scrutiny.',
  'ART30-007': 'No exit/termination plan — transition risk crystallises at contract end or failure, with operational continuity potentially compromised.',
  'ART30-008': 'Customer data not portable — in event of supplier failure or termination, data retrieval may be blocked, increasing recovery time significantly.',
  'ART30-009': 'Business continuity obligations not specified — supplier not contractually required to maintain resilience capabilities required under DORA.',
  'ART30-010': 'No obligation to test continuity/recovery plans — supplier resilience is unverified and cannot be relied upon for DORA operational resilience.',
  'ART30-011': 'No change management or notification requirements — supplier may change infrastructure/processes without customer awareness, introducing uncontrolled risk.',
  'ART30-012': 'No capacity management provisions — supplier may not scale to meet the financial entity\'s needs, creating availability and performance risk.',
  'ART30-013': 'No data processing agreement — GDPR Art. 28 violation; potential supervisory authority enforcement and fines up to 2% of global annual turnover.',
};
function getConsequence(reqId) {
  return CONSEQUENCES[reqId] || 'Non-compliance with DORA/GDPR contractual obligation — regulatory exposure and potential supervisory action.';
}

// ── Contract summary modal ────────────────────────────────────────────────
function showContractModal(contractKey) {
  const a = analyses.find(x => (x.contract_key || x.analysis_id) === contractKey);
  if (!a) return;
  const assessments = a.assessments || [];
  const missing = assessments.filter(x => x.status === 'missing' || x.status === 'ambiguous');
  const cs = controlStrength(missing.length);
  const csClass = controlStrengthClass(missing.length);
  const rr = residualRisk(a.classification, missing.length);
  const rrEmoji = rr === 'High' ? '🔴' : rr === 'Medium' ? '🟠' : '🟢';
  const classLabel = {'critical_important':'Critical / Important','non_critical':'Non-Critical','not_classified':'Not Classified'}[a.classification] || a.classification || '—';
  const isDORA = assessments.some(x => (x.requirement_id||'').startsWith('ART30'));
  const isGDPR = assessments.some(x => (x.requirement_id||'').startsWith('ART28'));
  const legLine = [isDORA ? 'DORA Art. 30' : '', isGDPR ? 'GDPR Art. 28' : ''].filter(Boolean).join(' · ');

  const content = document.getElementById('contract-modal-content');
  content.innerHTML = `
    <h3 style="margin:0 0 4px">${a.supplier_name}</h3>
    <p style="color:var(--muted);font-size:12px;margin:0 0 16px">${a.contract_reference||''}${legLine ? ' &nbsp;·&nbsp; ' + legLine : ''}</p>
    <div class="summary-cards" style="margin-bottom:20px">
      <div class="card"><div class="label">Classification</div><div style="font-size:13px;margin-top:4px">${classBadge(a.classification)}</div></div>
      <div class="card"><div class="label">Missing / Ambiguous</div><div class="value ${missing.length > 0 ? 'red' : 'green'}">${missing.length}</div></div>
      <div class="card"><div class="label">Control Strength</div><div class="value ${csClass}" style="font-size:13px">${cs}</div></div>
      <div class="card"><div class="label">Residual Risk</div><div class="value" style="font-size:16px">${rrEmoji} ${rr}</div></div>
    </div>
    ${missing.length ? `
    <div class="section-title" style="margin-bottom:8px">Missing / Ambiguous Requirements (${missing.length})</div>
    <ol style="margin:0;padding-left:20px;font-size:12px;color:var(--text);line-height:1.8">
      ${missing.map((m,i) => `<li><strong style="color:var(--accent)">${m.requirement_article||m.requirement_id}</strong> — ${m.requirement_summary||''}</li>`).join('')}
    </ol>` : '<p style="color:var(--green);font-size:13px">&#10003; No missing requirements identified.</p>'}
    <div style="margin-top:20px;text-align:right">
      <button onclick="document.getElementById('contract-modal').classList.remove('open');showTab('contracts');showContractDetail('${contractKey}')" style="background:var(--accent);color:#fff;border:none;padding:8px 18px;border-radius:6px;cursor:pointer;font-size:13px">View Full Analysis &#8594;</button>
    </div>
  `;
  document.getElementById('contract-modal').classList.add('open');
}

// ── Open contract detail from register tab ───────────────────────────────
function openContractForSupplier(supplierName, evt) {
  if (evt) evt.stopPropagation();
  const a = analyses.find(x => x.supplier_name && x.supplier_name.toLowerCase() === supplierName.toLowerCase());
  if (!a) {
    alert('No contract analysis found for ' + supplierName + '. Check the Contracts tab.');
    return;
  }
  const key = a.contract_key || a.analysis_id;
  showContractModal(key);
}

// ── Board Report ──────────────────────────────────────────────────────────
async function loadBoardReport() {
  const container = document.getElementById('boardreport-content');
  let wpData, reconData;
  try {
    [wpData, reconData] = await Promise.all([
      fetch('/api/workpapers').then(r => r.json()),
      fetch('/api/reconciliation').then(r => r.json()),
    ]);
  } catch(e) {
    container.innerHTML = '<div class="empty">Could not load board report data.</div>';
    return;
  }
  if (wpData.message && !wpData.work_papers) {
    container.innerHTML = '<div class="empty">' + wpData.message + '</div>';
    return;
  }

  const wps = wpData.work_papers || [];
  const openFindings = wps.flatMap(w => (w.findings||[]).filter(f => f.status === 'open'));
  const closedFindings = wps.flatMap(w => (w.findings||[]).filter(f => f.status === 'closed'));
  const sevColor = s => ({CRITICAL:'var(--red)',HIGH:'var(--orange)',MEDIUM:'var(--yellow)',LOW:'var(--green)'}[s]||'var(--muted)');
  const priSort = {CRITICAL:0,HIGH:1,MEDIUM:2,LOW:3};
  openFindings.sort((a,b) => (priSort[a.severity]??9) - (priSort[b.severity]??9));

  const recon = reconData.summary || {};

  container.innerHTML = `
    <!-- Executive summary cards -->
    <div class="summary-cards" style="margin-bottom:24px">
      <div class="card"><div class="label">Total Controls Tested</div><div class="value">${wpData.tested_count||0} / ${wpData.total_controls||0}</div></div>
      <div class="card"><div class="label">Exceptions</div><div class="value ${(wpData.exception_count||0)>0?'yellow':''}">${wpData.exception_count||0}</div></div>
      <div class="card"><div class="label">Open Findings</div><div class="value ${openFindings.length>0?'red':''}">${openFindings.length}</div></div>
      <div class="card"><div class="label">Closed Findings</div><div class="value green">${closedFindings.length}</div></div>
      <div class="card"><div class="label">Register Gaps</div><div class="value ${(recon.register_only||0)>0?'red':''}">${recon.register_only||0}</div></div>
    </div>

    <!-- Open findings -->
    ${openFindings.length ? `
    <div class="board-section">
      <div class="section-title" style="color:var(--orange);margin-bottom:12px">&#9888; Open Findings Requiring Follow-Up (${openFindings.length})</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Finding</th><th>Severity</th><th>Owner</th><th>Due</th><th>Condition (5-C)</th><th>Recommendation</th></tr></thead>
        <tbody>${openFindings.map(f => `<tr>
          <td style="font-size:12px;font-weight:600">${f.finding_id}: ${f.title}</td>
          <td><span class="badge" style="background:var(--surface2);color:${sevColor(f.severity)}">${f.severity}</span></td>
          <td style="font-size:12px;color:var(--muted)">${f.owner||'—'}</td>
          <td style="font-size:12px;color:${f.target_remediation_date?'var(--yellow)':'var(--muted)'}">${f.target_remediation_date||'TBD'}</td>
          <td style="font-size:12px;max-width:200px">${f.condition||'—'}</td>
          <td style="font-size:12px;color:var(--accent);max-width:200px">${f.recommendation||'—'}</td>
        </tr>`).join('')}</tbody>
      </table></div>
    </div>` : '<div class="board-section"><p style="color:var(--green)">&#10003; No open findings.</p></div>'}

    <!-- Audit reports summary -->
    <div class="board-section" style="margin-top:24px">
      <div class="section-title" style="margin-bottom:12px">Audit Working Papers — Period Summary</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Control ID</th><th>Title</th><th>Domain</th><th>Risk</th><th>Test Result</th><th>Findings</th></tr></thead>
        <tbody>${wps.map(w => `<tr>
          <td><code style="font-size:11px">${w.control_id}</code></td>
          <td style="font-size:12px">${w.control_title}</td>
          <td style="font-size:11px;color:var(--muted)">${w.control_domain||''}</td>
          <td style="font-size:12px;font-weight:600;color:${({CRITICAL:'var(--red)',HIGH:'var(--orange)',MEDIUM:'var(--yellow)',LOW:'var(--green)'}[w.inherent_risk]||'var(--muted)')}">${w.inherent_risk||'—'}</td>
          <td style="font-size:12px">${({'PASSED':'<span style="color:var(--green)">&#10003; Passed</span>','EXCEPTION':'<span style="color:var(--orange)">&#9888; Exception</span>','NOT_TESTED':'<span style="color:var(--muted)">Not Tested</span>','NOT_APPLICABLE':'<span style="color:var(--muted)">N/A</span>'}[w.test_result]||w.test_result)}</td>
          <td style="font-size:12px">${w.open_finding_count > 0 ? '<span style="color:var(--red)">'+w.open_finding_count+' open</span>' : w.total_finding_count > 0 ? '<span style="color:var(--green)">'+w.total_finding_count+' closed</span>' : '<span style="color:var(--muted)">—</span>'}</td>
        </tr>`).join('')}</tbody>
      </table></div>
    </div>

    <!-- ICT Register status -->
    <div class="board-section" style="margin-top:24px">
      <div class="section-title" style="margin-bottom:12px">ICT Register Status — DORA Art. 28(3)</div>
      <div class="summary-cards">
        <div class="card"><div class="label">Total in Register</div><div class="value">${recon.total_register_entries||0}</div></div>
        <div class="card"><div class="label">Assessed</div><div class="value green">${recon.matched||0}</div></div>
        <div class="card"><div class="label">Pending Assessment</div><div class="value ${(recon.register_only||0)>0?'red':''}">${recon.register_only||0}</div></div>
        <div class="card"><div class="label">Not in Register</div><div class="value ${(recon.analysis_only||0)>0?'yellow':''}">${recon.analysis_only||0}</div></div>
      </div>
    </div>
  `;
}

async function loadOverview() {
  const [analysisRes, registerRes, reconRes] = await Promise.all([
    fetch('/api/analyses'),
    fetch('/api/register'),
    fetch('/api/reconciliation'),
  ]);
  analyses = await analysisRes.json();
  registerEntries = await registerRes.json();
  const recon = await reconRes.json();

  // Summary cards
  const gaps = analyses.filter(a => a.overall_status !== 'compliant').length;
  const compliant = analyses.filter(a => a.overall_status === 'compliant').length;
  document.getElementById('stat-total').textContent = analyses.length;
  document.getElementById('stat-gaps').textContent = gaps;
  document.getElementById('stat-compliant').textContent = compliant;
  document.getElementById('stat-register').textContent = registerEntries.length;
  const reconGaps = recon.summary ? recon.summary.register_only : '—';
  document.getElementById('stat-recon-gaps').textContent = reconGaps || 0;

  // Overview table
  const tbody = document.getElementById('overview-table');
  if (!analyses.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No analyses loaded</td></tr>';
  } else {
    tbody.innerHTML = analyses.map(a => {
      const miss = a.summary ? (a.summary.missing || 0) : 0;
      const cs = controlStrength(miss);
      const csC = controlStrengthClass(miss);
      const rr = residualRisk(a.classification, miss);
      const rrC = residualRiskClass(a.classification, miss);
      return `<tr onclick="showContractModal('${(a.contract_key || a.analysis_id).replace(/'/g,"\\'")}')" style="cursor:pointer">
        <td>${a.supplier_name}</td>
        <td>${classBadge(a.classification)}</td>
        <td>${statusBadge(a.overall_status)}</td>
        <td>${miss}</td>
        <td>${a.summary ? a.summary.satisfied || 0 : 0}</td>
        <td><span class="badge ${csC}">${cs}</span></td>
        <td><span class="badge ${rrC}">${rr}</span></td>
      </tr>`;
    }).join('');
  }
}

async function loadContracts() {
  const tbody = document.getElementById('contracts-table');
  if (!analyses.length) {
    tbody.innerHTML = '<tr><td colspan="8" class="empty">No analyses loaded</td></tr>';
    return;
  }
  tbody.innerHTML = analyses.map(a => {
    const miss = a.summary ? (a.summary.missing || 0) : 0;
    const cs = controlStrength(miss);
    const csC = controlStrengthClass(miss);
    const rr = residualRisk(a.classification, miss);
    const rrC = residualRiskClass(a.classification, miss);
    return `<tr onclick="showContractDetail('${a.contract_key || a.analysis_id}')">
      <td><code>${a.analysis_id}</code></td>
      <td>${a.supplier_name}</td>
      <td style="color:var(--muted);font-size:12px">${(a.contract_path.replace(/\\\\/g,'/').split('/').pop())}</td>
      <td>${classBadge(a.classification)}</td>
      <td>${statusBadge(a.overall_status)}</td>
      <td>${miss}</td>
      <td><span class="badge ${csC}">${cs}</span></td>
      <td><span class="badge ${rrC}">${rr}</span></td>
    </tr>`;
  }).join('');
}

async function showContractDetail(id) {
  const panel = document.getElementById('contract-detail');
  panel.className = 'detail-panel open';
  panel.innerHTML = '<div class="loading" style="padding:20px">Loading&#8230;</div>';

  const [detailRes, assessRes] = await Promise.all([
    fetch('/api/analyses/' + encodeURIComponent(id)),
    fetch('/api/analyses/' + encodeURIComponent(id) + '/assessments'),
  ]);
  if (!detailRes.ok) { panel.innerHTML = '<div class="empty">Not found.</div>'; return; }
  const data = await detailRes.json();
  const assessData = assessRes.ok ? await assessRes.json() : {assessments: []};
  const assessments = assessData.assessments || [];

  const miss = data.summary ? (data.summary.missing || 0) : 0;
  const cs = controlStrength(miss);
  const csC = controlStrengthClass(miss);
  const rr = residualRisk(data.classification, miss);
  const rrC = residualRiskClass(data.classification, miss);

  const statusLabel = s => ({missing:'Mangler',ambiguous:'Tvetydig',satisfied:'Opfyldt',na:'N/A',not_assessed:'Ikke vurderet'}[s]||s);
  const statusCls   = s => ({missing:'status-missing',ambiguous:'status-ambiguous',satisfied:'status-satisfied',na:'status-na'}[s]||'');
  const riskLabel   = r => r ? r.replace(/_/g,' ') : '—';

  // Legislation banner
  const isDORA = assessments.some(a=>(a.requirement_id||'').startsWith('ART30'));
  const isGDPR = assessments.some(a=>(a.requirement_id||'').startsWith('ART28'));
  const legBanner = (isDORA ? '<span class="leg-banner leg-dora">DORA — Digital Operational Resilience Act (Reg. (EU) 2022/2554)</span>' : '')
    + (isGDPR ? '<span class="leg-banner leg-gdpr">GDPR Art. 28 — Data Processing Agreement</span>' : '');

  const findingsRows = assessments.filter(a => a.status !== 'satisfied' && a.status !== 'na').map(a => `
    <tr>
      <td style="font-size:12px;white-space:nowrap;font-weight:600;color:var(--accent)">${a.requirement_article||a.requirement_id||''}</td>
      <td style="font-size:12px">${a.requirement_summary||''}</td>
      <td class="${statusCls(a.status)}" style="font-size:12px;white-space:nowrap">${statusLabel(a.status)}</td>
      <td style="font-size:12px">${riskLabel(a.risk_level)}</td>
      <td style="font-size:12px;color:var(--muted)">${a.gap_description||'—'}</td>
      <td class="consequence-cell">${getConsequence(a.requirement_id)}</td>
      <td style="font-size:12px;color:var(--accent)">${a.remediation_suggestion||'—'}</td>
    </tr>`).join('');

  const hasFindings = findingsRows.length > 0;

  panel.innerHTML = `
    <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:12px;gap:12px;flex-wrap:wrap">
      <div>
        <h3 style="margin-bottom:4px">${data.supplier_name} — ${data.analysis_id}</h3>
        <span style="font-size:12px;color:var(--muted)">${data.classification==='critical_important'?'Critical/Important':'Non-critical'} · ${data.overall_status}</span>
      </div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
        <span class="badge ${csC}">${cs}</span>
        <span class="badge ${rrC}">Residual Risk: ${rr}</span>
      </div>
    </div>
    ${legBanner ? `<div style="margin-bottom:14px">${legBanner}</div>` : ''}
    ${data.warnings && data.warnings.length ? `<p style="color:var(--orange);margin-bottom:12px;font-size:13px">&#9888; ${data.warnings.join(' | ')}</p>` : ''}
    ${hasFindings ? `
      <div style="margin-bottom:16px">
        <div class="section-title" style="margin-bottom:8px">Manglende/tvetydige krav (${assessments.filter(a=>a.status!=='satisfied'&&a.status!=='na').length})</div>
        <div style="overflow-x:auto"><table class="findings-table">
          <thead><tr>
            <th>Article</th><th>Beskrivelse</th><th>Status</th><th>Risiko</th><th>Hvad mangler</th><th>Konsekvens</th><th>Anbefalet Remedy</th>
          </tr></thead>
          <tbody>${findingsRows}</tbody>
        </table></div>
      </div>` : '<p style="color:var(--green);margin-bottom:16px;font-size:13px">&#10003; Alle krav er opfyldt eller N/A.</p>'}
    <details style="margin-top:8px">
      <summary style="cursor:pointer;color:var(--muted);font-size:12px;user-select:none">&#9654; Vis fuld analyserapport (Markdown)</summary>
      <pre style="margin-top:8px">${(data.markdown_report||'(ingen rapport)').replace(/</g,'&lt;')}</pre>
    </details>
  `;
}

async function loadRegister() {
  const tbody = document.getElementById('register-table');
  if (!registerEntries.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No register loaded</td></tr>';
    return;
  }
  tbody.innerHTML = registerEntries.map(e => `<tr>
    <td><code>${e.entry_id}</code></td>
    <td>${e.supplier_name}</td>
    <td>${e.supplier_country}</td>
    <td style="color:var(--muted);font-size:12px">${e.service_type.replace(/_/g, ' ')}</td>
    <td style="font-size:12px"><button onclick="openContractForSupplier('${e.supplier_name.replace(/'/g,"\\'")}',event)" style="background:none;border:none;color:var(--accent);cursor:pointer;font-family:monospace;font-size:12px;padding:0;text-decoration:underline">${e.contract_reference}</button></td>
    <td>${classBadge(e.classification)}</td>
    <td>${e.exit_strategy_documented
      ? `<span class="tooltip-wrap"><span style="color:var(--green)">&#10003;</span><span class="tooltip-box" style="width:220px">Exit strategy documented — DORA Art. 28(2)(e) requirement met.</span></span>`
      : `<span class="tooltip-wrap"><span style="color:var(--red)">&#10007;</span><span class="tooltip-box" style="width:240px">No exit strategy documented — required under DORA Art. 28(2)(e). Add an exit/migration plan to the contract before next renewal.</span></span>`}</td>
  </tr>`).join('');
}

async function loadReconciliation() {
  const res = await fetch('/api/reconciliation');
  const data = await res.json();
  const container = document.getElementById('recon-content');

  if (data.message) {
    container.innerHTML = `<div class="empty">${data.message}</div>`;
    return;
  }

  const s = data.summary || {};
  container.innerHTML = `
    <div class="summary-cards" style="margin-bottom:20px">
      <div class="card"><div class="label"><span class="tooltip-wrap">Total Register<span class="tooltip-icon">ⓘ</span><span class="tooltip-box" style="width:220px">Total number of ICT third-party providers in your DORA Art. 28(3) register.</span></span></div><div class="value">${s.total_register_entries || 0}</div></div>
      <div class="card"><div class="label"><span class="tooltip-wrap">Analyses<span class="tooltip-icon">ⓘ</span><span class="tooltip-box" style="width:220px">Number of contracts for which a DORA/GDPR gap analysis has been completed.</span></span></div><div class="value">${s.total_analyses || 0}</div></div>
      <div class="card"><div class="label"><span class="tooltip-wrap">Matched<span class="tooltip-icon">ⓘ</span><span class="tooltip-box" style="width:240px">Register entries with a matching contract analysis (by contract reference or supplier name).</span></span></div><div class="value green">${s.matched || 0}</div></div>
      <div class="card"><div class="label"><span class="tooltip-wrap">Register Only<span class="tooltip-icon">ⓘ</span><span class="tooltip-box" style="width:260px">Providers in the register with no contract analysis — DORA requires these to be assessed before renewal or at next review.</span></span></div><div class="value red">${s.register_only || 0}</div></div>
      <div class="card"><div class="label"><span class="tooltip-wrap">Contract Only<span class="tooltip-icon">ⓘ</span><span class="tooltip-box" style="width:260px">Contracts with an analysis but no matching register entry — these suppliers must be added to the ICT register.</span></span></div><div class="value yellow">${s.analysis_only || 0}</div></div>
    </div>

    ${data.matched && data.matched.length ? `
    <div class="recon-section">
      <div class="section-title">Matched (register &#8596; contract)</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Supplier</th><th>Contract Ref</th><th>Classification</th><th>Status</th><th>Missing</th><th>Match via</th></tr></thead>
        <tbody>${data.matched.map(m => `<tr>
          <td>${m.register_entry.supplier_name}</td>
          <td><code>${m.register_entry.contract_reference}</code></td>
          <td>${classBadge(m.register_entry.classification)}</td>
          <td>${statusBadge(m.overall_status)}</td>
          <td>${m.missing_count}</td>
          <td style="color:var(--muted);font-size:12px">${m.match_method}</td>
        </tr>`).join('')}</tbody>
      </table></div>
    </div>` : ''}

    ${data.register_only && data.register_only.length ? `
    <div class="recon-section">
      <div class="section-title" style="color:var(--red)">&#9888; Register entries without contract assessment</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Supplier</th><th>Contract Ref</th><th>Classification</th><th>Service</th></tr></thead>
        <tbody>${data.register_only.map(e => `<tr>
          <td>${e.supplier_name}</td>
          <td><code>${e.contract_reference}</code></td>
          <td>${classBadge(e.classification)}</td>
          <td style="color:var(--muted);font-size:12px">${e.service_type.replace(/_/g, ' ')}</td>
        </tr>`).join('')}</tbody>
      </table></div>
    </div>` : ''}

    ${data.analysis_only && data.analysis_only.length ? `
    <div class="recon-section">
      <div class="section-title" style="color:var(--yellow)">&#9888; Contracts not in register</div>
      <div class="table-wrap"><table>
        <thead><tr><th>Contract</th><th>Supplier</th><th>Analysis ID</th></tr></thead>
        <tbody>${data.analysis_only.map(a => `<tr>
          <td style="font-size:12px">${a.contract_path || ''}</td>
          <td>${a.supplier_name || ''}</td>
          <td><code>${a.analysis_id || ''}</code></td>
        </tr>`).join('')}</tbody>
      </table></div>
    </div>` : ''}
  `;
}

async function loadWorkPapers() {
  const container = document.getElementById('wp-content');
  let data;
  try {
    const res = await fetch('/api/workpapers');
    data = await res.json();
  } catch(e) {
    container.innerHTML = '<div class="empty">Could not load working papers.</div>';
    return;
  }
  if (data.message) {
    container.innerHTML = `<div class="empty">${data.message}</div>`;
    return;
  }

  const riskClass = r => ({CRITICAL:'risk-critical',HIGH:'risk-high',MEDIUM:'risk-medium',LOW:'risk-low'}[r]||'');
  const resultIcon = r => ({PASSED:'✅',EXCEPTION:'⚠️',NOT_TESTED:'⬜',NOT_APPLICABLE:'➖'}[r]||r);
  const resultClass = r => r==='EXCEPTION'?'result-exception':r==='PASSED'?'result-passed':'result-not_tested';

  container.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
      <div>
        <span style="color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em">Engagement</span>
        <span style="margin-left:8px;font-size:13px"><code>${data.engagement_id}</code> — ${data.organisation}</span>
      </div>
      <a class="btn" href="/api/workpapers/report" target="_blank">&#11015; Download Audit Report</a>
    </div>
    <div class="summary-cards" style="margin-bottom:20px">
      <div class="card"><div class="label">Controls in scope</div><div class="value">${data.total_controls}</div></div>
      <div class="card"><div class="label">Tested</div><div class="value">${data.tested_count}</div></div>
      <div class="card"><div class="label">Passed</div><div class="value green">${data.passed_count}</div></div>
      <div class="card"><div class="label">Exceptions</div><div class="value ${data.exception_count>0?'yellow':''}}">${data.exception_count}</div></div>
      <div class="card"><div class="label">Open findings</div><div class="value ${data.open_findings>0?'red':''}}">${data.open_findings}</div></div>
    </div>
    <div id="wp-heatmap" style="margin-bottom:20px"></div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Control ID</th><th>Title</th><th>Domain</th><th>Inherent Risk</th><th>Result</th><th>Findings</th><th>Control Strength</th><th>Residual Risk</th>
        </tr></thead>
        <tbody>${data.work_papers.map(wp => {
          const cs = controlStrengthWP(wp.open_finding_count);
          const csC = controlStrengthClassWP(wp.open_finding_count);
          const rr = residualRiskWP(wp.inherent_risk, wp.open_finding_count);
          const rrC = residualRiskClassWP(wp.inherent_risk, wp.open_finding_count);
          return `<tr onclick="showWPDetail('${wp.control_id}')" style="cursor:pointer">
            <td><code>${wp.control_id}</code></td>
            <td>${wp.control_title}</td>
            <td style="color:var(--muted);font-size:12px">${wp.control_domain}</td>
            <td class="${riskClass(wp.inherent_risk)}">${wp.inherent_risk}</td>
            <td class="${resultClass(wp.test_result)}">${resultIcon(wp.test_result)} ${wp.test_result.replace(/_/g,' ')}</td>
            <td>${wp.open_finding_count>0?`<span style="color:var(--orange)">${wp.finding_count} (${wp.open_finding_count} open)</span>`:wp.finding_count>0?wp.finding_count:''}</td>
            <td><span class="badge ${csC}">${cs}</span></td>
            <td><span class="badge ${rrC}">${rr}</span></td>
          </tr>`;
        }).join('')}</tbody>
      </table>
    </div>
    <div id="wp-detail" class="detail-panel"></div>
  `;
  await loadHeatmap();
}

async function loadHeatmap() {
  const el = document.getElementById('wp-heatmap');
  if (!el) return;
  let hm;
  try {
    const res = await fetch('/api/risk-heatmap');
    hm = await res.json();
  } catch(e) { return; }
  if (hm.message) return;

  const riskColors = {CRITICAL:'#c0392b',HIGH:'#e67e22',MEDIUM:'#f1c40f',LOW:'#27ae60'};
  const colLabels = {PASSED:'Passed',EXCEPTION:'Exception',NOT_TESTED:'Not Tested',NOT_APPLICABLE:'N/A'};
  const colColors = {PASSED:'#27ae60',EXCEPTION:'#c0392b',NOT_TESTED:'#95a5a6',NOT_APPLICABLE:'#bdc3c7'};

  let hdr = `<th style="font-size:11px;padding:6px 10px;text-align:left;color:var(--muted);border-bottom:1px solid var(--border)">InherentRisk \\ Result</th>`;
  for (const tr of hm.test_results) {
    hdr += `<th style="font-size:11px;padding:6px 10px;text-align:center;color:${colColors[tr]};border-bottom:1px solid var(--border)">${colLabels[tr]||tr}</th>`;
  }

  let rows = '';
  for (const risk of hm.risk_levels) {
    let cells = `<td style="font-size:12px;padding:6px 10px;font-weight:600;color:${riskColors[risk]||'var(--text)'};border-bottom:1px solid var(--border)">${risk}</td>`;
    for (const tr of hm.test_results) {
      const count = (hm.matrix[risk]||{})[tr] || 0;
      let bg = 'transparent';
      let textCol = 'var(--muted)';
      if (count > 0) {
        if (tr === 'EXCEPTION') {
          bg = risk === 'CRITICAL' ? 'rgba(192,57,43,0.25)' : risk === 'HIGH' ? 'rgba(230,126,34,0.20)' : 'rgba(241,196,15,0.15)';
          textCol = colColors[tr];
        } else if (tr === 'PASSED') {
          bg = 'rgba(39,174,96,0.15)';
          textCol = colColors[tr];
        } else {
          bg = 'rgba(149,165,166,0.12)';
          textCol = 'var(--text)';
        }
      }
      cells += `<td style="text-align:center;padding:6px 10px;background:${bg};color:${textCol};font-size:14px;font-weight:${count>0?'600':'400'};border-bottom:1px solid var(--border)">${count > 0 ? count : '—'}</td>`;
    }
    rows += `<tr>${cells}</tr>`;
  }

  el.innerHTML = `
    <div class="section-title" style="margin-bottom:8px">Risk Heatmap — Inherent Risk × Test Result</div>
    <div style="overflow-x:auto">
      <table style="border-collapse:collapse;min-width:400px;background:var(--surface);border-radius:8px;overflow:hidden;border:1px solid var(--border)">
        <thead><tr>${hdr}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <p style="font-size:11px;color:var(--muted);margin-top:6px">${hm.total} controls total. Red cells = exceptions on high/critical controls requiring immediate attention.</p>
  `;
}

async function showWPDetail(controlId) {
  const panel = document.getElementById('wp-detail');
  panel.className = 'detail-panel open';
  panel.innerHTML = '<div class="loading" style="padding:20px">Loading&#8230;</div>';
  const res = await fetch('/api/workpapers/' + encodeURIComponent(controlId));
  if (!res.ok) { panel.innerHTML = '<div class="empty">Not found.</div>'; return; }
  const wp = await res.json();
  const severityColor = s => ({CRITICAL:'var(--red)',HIGH:'var(--orange)',MEDIUM:'var(--yellow)',LOW:'var(--green)',INFO:'var(--muted)'}[s]||'var(--muted)');
  const statusColor = s => s==='open'?'var(--orange)':s==='closed'?'var(--green)':'var(--muted)';

  // Audit declaration text — 3 scenarios based on test_result
  let declarationLabel, declarationText;
  if (wp.test_result === 'PASSED') {
    declarationLabel = 'Scenario A — Design & Implementation (D&I) Only';
    declarationText = `Based on our review of documentation and design evidence, we conclude that the control <strong>${wp.control_id} — ${wp.control_title}</strong> is suitably designed and has been implemented as intended. Our audit procedure covered design assessment and implementation verification. No effectiveness testing (ISAE 3402 Type II or equivalent) was performed in this engagement period. The control achieves its stated objective: <em>${wp.audit_objective||'see control description'}</em>.`;
  } else if (wp.test_result === 'EXCEPTION') {
    declarationLabel = 'Scenario B — Effectiveness Testing with Exception (ISAE 3402 / Internal Audit)';
    declarationText = `We performed effectiveness testing of control <strong>${wp.control_id} — ${wp.control_title}</strong> over the engagement period. Our audit procedure included inspection of evidence, re-performance where applicable, and inquiry of control owners. <strong style="color:var(--orange)">One or more exceptions were identified</strong> during the testing period. The exceptions are detailed in the findings section below. Where a third-party assurance report (ISAE 3402, SOC 2 Type II or equivalent) was reviewed, our conclusions take into account any qualified opinion or noted deviations in that report.`;
  } else {
    declarationLabel = 'Scenario C — Not Tested in This Period';
    declarationText = `Control <strong>${wp.control_id} — ${wp.control_title}</strong> was scoped into the audit universe but was <strong style="color:var(--muted)">not tested</strong> during this engagement period. This may reflect a risk-based decision to defer testing, reliance on a prior-period assessment, or pending availability of evidence. The control remains subject to future audit cycles. Classification: Inherent Risk <strong>${wp.inherent_risk}</strong>.`;
  }

  panel.innerHTML = `
    <h3><code>${wp.control_id}</code> — ${wp.control_title}</h3>
    <p style="color:var(--muted);font-size:12px;margin:6px 0 16px">${wp.control_domain} · Inherent Risk: <strong style="color:${({'CRITICAL':'var(--red)','HIGH':'var(--orange)','MEDIUM':'var(--yellow)','LOW':'var(--green)'}[wp.inherent_risk]||'var(--muted)')}}">${wp.inherent_risk}</strong> · Result: <strong>${wp.test_result.replace(/_/g,' ')}</strong></p>

    <div class="wp-text-block" style="margin-bottom:16px">
      <div class="wp-declaration-label">${declarationLabel}</div>
      <p style="margin:8px 0 0;line-height:1.7;font-size:13px">${declarationText}</p>
    </div>

    ${wp.audit_objective ? `<div style="margin-bottom:12px"><span style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)">Audit Procedure</span><p style="font-size:13px;margin:4px 0 0">${wp.audit_objective}</p></div>` : ''}

    ${wp.expected_evidence_artifacts && wp.expected_evidence_artifacts.length ? `
    <div style="margin-bottom:12px">
      <span style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)">Expected Evidence Reviewed</span>
      <ul style="margin:4px 0 0;padding-left:18px;font-size:12px;color:var(--text)">
        ${wp.expected_evidence_artifacts.map(e => `<li style="margin-bottom:2px">${e}</li>`).join('')}
      </ul>
    </div>` : ''}

    ${wp.testing_notes ? `<div style="margin-bottom:16px"><span style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)">Testing Notes</span><p style="font-size:12px;margin:4px 0 0;color:var(--muted)">${wp.testing_notes}</p></div>` : ''}

    ${wp.findings.length ? `
      <div style="margin-top:16px">
        <div class="section-title">Findings — 5-C Format (${wp.findings.length})</div>
        ${wp.findings.map(f => `
          <div class="finding-row">
            <h4>${f.finding_id}: ${f.title}</h4>
            <div class="finding-meta">
              <span class="badge" style="background:var(--surface2);color:${severityColor(f.severity)}">${f.severity}</span>
              <span class="badge" style="background:var(--surface2);color:${statusColor(f.status)}">${f.status}</span>
              ${f.owner ? `<span style="font-size:12px;color:var(--muted)">Owner: ${f.owner}</span>` : ''}
              ${f.target_remediation_date ? `<span style="font-size:12px;color:var(--muted)">Due: ${f.target_remediation_date}</span>` : ''}
            </div>
            <div class="five-c">
              <div><strong>Condition:</strong> ${f.condition}</div>
              <div><strong>Criteria:</strong> ${f.criteria}</div>
              <div><strong>Cause:</strong> ${f.cause}</div>
              <div><strong>Effect:</strong> ${f.effect}</div>
              <div><strong>Recommendation:</strong> ${f.recommendation}</div>
            </div>
          </div>`).join('')}
      </div>` : '<p style="color:var(--muted);font-size:13px;margin-top:12px">No findings recorded for this control.</p>'}
  `;
}

async function uploadContract(input) {
  const file = input.files[0];
  if (!file) return;
  const statusEl = document.getElementById('upload-status');
  statusEl.style.display = 'block';
  statusEl.style.background = 'var(--surface2)';
  statusEl.style.color = 'var(--muted)';
  statusEl.style.border = '1px solid var(--border)';
  statusEl.textContent = 'Analysing ' + file.name + '…';
  input.value = '';
  const fd = new FormData();
  fd.append('file', file);
  try {
    const res = await fetch('/api/analyse', {method:'POST', body: fd});
    const data = await res.json();
    if (!res.ok) {
      statusEl.style.background = '#2d1515';
      statusEl.style.color = 'var(--red)';
      statusEl.textContent = 'Error: ' + (data.detail || res.statusText);
      return;
    }
    statusEl.style.background = '#0d2a0d';
    statusEl.style.color = 'var(--green)';
    statusEl.textContent = 'Done — ' + data.supplier_name + ' (' + data.analysis_id + '): ' + (data.overall_status || '');
    analyses.push(data);
    await loadContracts();
    document.getElementById('stat-total').textContent = analyses.length;
  } catch(e) {
    statusEl.style.color = 'var(--red)';
    statusEl.textContent = 'Upload failed: ' + e.message;
  }
}

async function loadAuditIntelligence() {
  const container = document.getElementById('audit-content');
  let wpData;
  try {
    const res = await fetch('/api/workpapers');
    wpData = await res.json();
  } catch(e) {
    container.innerHTML = '<div class="empty">Could not load audit data.</div>';
    return;
  }

  if (wpData.message) {
    container.innerHTML = '<div class="empty">' + wpData.message + '</div>';
    return;
  }

  const riskClass = r => ({CRITICAL:'risk-critical',HIGH:'risk-high',MEDIUM:'risk-medium',LOW:'risk-low'}[r]||'');
  const resultIcon = r => ({PASSED:'<span style="color:var(--green)">✅</span>',EXCEPTION:'<span style="color:var(--orange)">⚠️</span>',NOT_TESTED:'<span style="color:var(--muted)">⬜</span>',NOT_APPLICABLE:'<span style="color:var(--muted)">➖</span>'}[r]||r);

  let hmHtml = '';
  try {
    const hmRes = await fetch('/api/risk-heatmap');
    const hm = await hmRes.json();
    if (!hm.message) {
      const riskColors = {CRITICAL:'#c0392b',HIGH:'#e67e22',MEDIUM:'#f1c40f',LOW:'#27ae60'};
      const colColors = {PASSED:'#27ae60',EXCEPTION:'#c0392b',NOT_TESTED:'#95a5a6',NOT_APPLICABLE:'#bdc3c7'};
      const colLabels = {PASSED:'Passed',EXCEPTION:'Exception',NOT_TESTED:'Not Tested',NOT_APPLICABLE:'N/A'};
      let hdr = '<th style="font-size:11px;padding:6px 10px;color:var(--muted);border-bottom:1px solid var(--border)">Risk / Result</th>';
      for (const tr of hm.test_results) hdr += '<th style="font-size:11px;padding:6px 10px;text-align:center;color:' + (colColors[tr]||'var(--muted)') + ';border-bottom:1px solid var(--border)">' + (colLabels[tr]||tr) + '</th>';
      let rows = '';
      for (const risk of hm.risk_levels) {
        let cells = '<td style="font-size:12px;padding:6px 10px;font-weight:600;color:' + (riskColors[risk]||'var(--text)') + ';border-bottom:1px solid var(--border)">' + risk + '</td>';
        for (const tr of hm.test_results) {
          const count = (hm.matrix[risk]||{})[tr] || 0;
          let bg = 'transparent', textCol = 'var(--muted)';
          if (count > 0) {
            if (tr === 'EXCEPTION') { bg = risk==='CRITICAL'?'rgba(192,57,43,0.25)':risk==='HIGH'?'rgba(230,126,34,0.20)':'rgba(241,196,15,0.15)'; textCol = colColors[tr]; }
            else if (tr === 'PASSED') { bg = 'rgba(39,174,96,0.15)'; textCol = colColors[tr]; }
            else { bg = 'rgba(149,165,166,0.12)'; textCol = 'var(--text)'; }
          }
          cells += '<td style="text-align:center;padding:6px 10px;background:' + bg + ';color:' + textCol + ';font-size:14px;font-weight:' + (count>0?'600':'400') + ';border-bottom:1px solid var(--border)">' + (count>0?count:'—') + '</td>';
        }
        rows += '<tr>' + cells + '</tr>';
      }
      hmHtml = '<div class="section-title" style="margin-bottom:8px">3×3 Risk Heatmap — Inherent Risk × Test Result</div><div style="overflow-x:auto"><table style="border-collapse:collapse;min-width:400px;background:var(--surface);border-radius:8px;overflow:hidden;border:1px solid var(--border)"><thead><tr>' + hdr + '</tr></thead><tbody>' + rows + '</tbody></table></div><p style="font-size:11px;color:var(--muted);margin-top:6px">' + hm.total + ' controls. Red = exceptions requiring attention.</p>';
    }
  } catch(e) {}

  const openFindings = wpData.work_papers.filter(w => w.open_finding_count > 0);

  container.innerHTML = '<div style="margin-bottom:4px"><span style="color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em">Engagement:</span> <code>' + wpData.engagement_id + '</code> — ' + wpData.organisation + ' <span style="color:var(--muted);font-size:12px">/ ' + wpData.scope + '</span></div>'
    + '<div class="summary-cards" style="margin:16px 0">'
    + '<div class="card"><div class="label">Audit Universe</div><div class="value">' + wpData.total_controls + '</div></div>'
    + '<div class="card"><div class="label">Tested</div><div class="value">' + wpData.tested_count + '</div></div>'
    + '<div class="card"><div class="label">Passed</div><div class="value green">' + wpData.passed_count + '</div></div>'
    + '<div class="card"><div class="label">Exceptions</div><div class="value ' + (wpData.exception_count>0?'yellow':'') + '">' + wpData.exception_count + '</div></div>'
    + '<div class="card"><div class="label">Open Findings</div><div class="value ' + (wpData.open_findings>0?'red':'') + '">' + wpData.open_findings + '</div></div>'
    + '</div>'
    + (hmHtml ? '<div style="margin-bottom:20px">' + hmHtml + '</div>' : '')
    + '<div class="section-title" style="margin-bottom:12px">Audit Universe (AU01–AU' + String(wpData.total_controls).padStart(2,'0') + ')</div>'
    + '<div class="table-wrap"><table><thead><tr><th>Control ID</th><th>Title</th><th>Domain</th><th>Risk</th><th>Result</th><th>Open Findings</th><th>Control Strength</th><th>Residual Risk</th></tr></thead><tbody>'
    + wpData.work_papers.map(wp => {
        const cs = controlStrengthWP(wp.open_finding_count);
        const csC = controlStrengthClassWP(wp.open_finding_count);
        const rr = residualRiskWP(wp.inherent_risk, wp.open_finding_count);
        const rrC = residualRiskClassWP(wp.inherent_risk, wp.open_finding_count);
        return '<tr data-id="' + wp.control_id + '" onclick="showWPDetail(this.dataset.id)" style="cursor:pointer">'
          + '<td><code>' + wp.control_id + '</code></td>'
          + '<td>' + wp.control_title + '</td>'
          + '<td style="color:var(--muted);font-size:12px">' + wp.control_domain + '</td>'
          + '<td class="' + riskClass(wp.inherent_risk) + '">' + wp.inherent_risk + '</td>'
          + '<td>' + resultIcon(wp.test_result) + ' ' + wp.test_result.replace(/_/g,' ') + '</td>'
          + '<td>' + (wp.open_finding_count>0?'<span style="color:var(--orange)">' + wp.open_finding_count + '</span>':'—') + '</td>'
          + '<td><span class="badge ' + csC + '">' + cs + '</span></td>'
          + '<td><span class="badge ' + rrC + '">' + rr + '</span></td>'
          + '</tr>';
      }).join('')
    + '</tbody></table></div>'
    + (openFindings.length ? '<div style="margin-top:20px"><div class="section-title" style="margin-bottom:12px">Open Findings Summary</div>'
      + openFindings.map(wp => '<div class="finding-row"><h4><code>' + wp.control_id + '</code> — ' + wp.control_title + '</h4><div class="finding-meta"><span class="badge" style="background:var(--surface2);color:var(--orange)">' + wp.open_finding_count + ' open finding' + (wp.open_finding_count>1?'s':'') + '</span><span class="badge badge-' + (wp.inherent_risk==='CRITICAL'?'critical':'gaps') + '">' + wp.inherent_risk + '</span></div></div>').join('')
      + '</div>' : '');
}

// Load data on page load
(async () => {
  await loadOverview();
  await Promise.all([loadContracts(), loadRegister(), loadReconciliation(), loadWorkPapers(), loadAuditIntelligence(), loadBoardReport()]);
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _load_results_from_dir(results_dir: pathlib.Path) -> list[PipelineResult]:
    """Load PipelineResult-compatible objects from JSON files in a directory."""
    results = []
    for json_file in sorted(results_dir.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
            # Accept files that look like analysis results
            if "analysis_id" not in data:
                continue
            results.append(PipelineResult(
                analysis_id=data.get("analysis_id", ""),
                contract_path=data.get("contract_path", str(json_file)),
                supplier_name=data.get("supplier_name", "Unknown"),
                classification=data.get("classification", "non_critical"),
                overall_status=data.get("overall_status", ""),
                summary=data.get("summary", {}),
                output_paths={},
                markdown_report=data.get("markdown_report", ""),
                warnings=data.get("warnings", []),
                assessments=data.get("assessments", []),
            ))
        except Exception:
            pass
    return results


def _load_demo_results() -> tuple[list[PipelineResult], list[RegisterEntry]]:
    """Load all 5 synthetic contracts and the sample register for demo mode."""
    contracts_dir = pathlib.Path(__file__).parent / "synthetic_contracts"
    register_path = contracts_dir / "register_sample.yaml"

    contract_specs = [
        {"path": "contract_01_cloudhost.md", "supplier": "NordCloud Hosting A/S",
         "classification": "critical_important", "contract_id": "NBS-ICT-2023-0047"},
        {"path": "contract_02_payment.md", "supplier": "PayLink Technologies Ltd",
         "classification": "critical_important", "contract_id": "FIN-PP-2022-091"},
        {"path": "contract_03_legacy.md", "supplier": "DataCenter DK ApS",
         "classification": "critical_important", "contract_id": "KAP-IT-2018-003"},
        {"path": "contract_04_saas.md", "supplier": "ComplianceTools International BV",
         "classification": "non_critical", "contract_id": "CT-SAAS-2024-0012"},
        {"path": "contract_05_good.md", "supplier": "SecureVault Nordic A/S",
         "classification": "critical_important", "contract_id": "NBI-ICT-2024-DOR-001"},
    ]

    pipeline = SupplierAssurancePipeline()
    results = []
    for spec in contract_specs:
        contract_path = contracts_dir / spec["path"]
        if not contract_path.exists():
            continue
        r = pipeline.run(
            contract_path=contract_path,
            supplier_name=spec["supplier"],
            customer_name="Demo Customer",
            classification=spec["classification"],
            contract_id=spec["contract_id"],
        )
        results.append(r)

    register = load_register(register_path) if register_path.exists() else []
    return results, register


def create_app(
    results: list[PipelineResult],
    register: list[RegisterEntry],
    customer_name: str = "Unknown Customer",
    work_paper_set: Optional[WorkPaperSet] = None,
) -> FastAPI:
    """Create and configure the app with pre-loaded data."""
    global _pipeline_results, _register, _reconciliation, _work_paper_set

    _pipeline_results = results
    _register = register
    _reconciliation = None
    _work_paper_set = work_paper_set

    if register and results:
        from .engine.register_reconciler import reconcile_from_pipeline_results
        _reconciliation = reconcile_from_pipeline_results(register, results, customer_name=customer_name)
    elif register:
        analyses_dicts = [
            {
                "analysis_id": r.analysis_id,
                "contract_path": r.contract_path,
                "supplier_name": r.supplier_name,
                "overall_status": r.overall_status,
                "summary": r.summary,
                "contract_id": r.analysis_id,
            }
            for r in results
        ]
        _reconciliation = reconcile(register, analyses_dicts, customer_name=customer_name)

    return app


def main():
    parser = argparse.ArgumentParser(description="Gamma7 Supplier Assurance Dashboard")
    parser.add_argument("--results-dir", type=pathlib.Path, default=None,
                        help="Directory containing analysis JSON files")
    parser.add_argument("--register", type=pathlib.Path, default=None,
                        help="YAML or CSV register file")
    parser.add_argument("--customer", default="Unknown Customer",
                        help="Customer name shown in reports")
    parser.add_argument("--demo", action="store_true",
                        help="Load all synthetic contracts + sample register")
    parser.add_argument("--workpapers", type=pathlib.Path, default=None,
                        help="JSON file of saved WorkPaperSet (from 'harvey audit workpapers --output')")
    parser.add_argument("--wp-org", default="Unknown Organisation",
                        help="Organisation name when generating workpapers on-the-fly")
    parser.add_argument("--wp-scope", default="iso27001",
                        help="Comma-separated audit scope keys (default: iso27001)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.demo:
        print("Loading demo data (5 synthetic contracts + sample register)…")
        results, register = _load_demo_results()
        print(f"  Loaded {len(results)} analyses, {len(register)} register entries")
        if not args.workpapers:
            from .audit_intelligence.audit_universe import build_universe
            from .audit_intelligence.workpaper import build_workpapers, TestResult, FindingSeverity, FindingStatus
            universe = build_universe(organisation="Demo Customer A/S", scope=["iso27001"])
            wps = build_workpapers(universe)
            # Stamp a realistic mix of test results so the heatmap is non-trivial
            _DEMO_RESULTS = [
                TestResult.PASSED, TestResult.PASSED, TestResult.PASSED,
                TestResult.EXCEPTION, TestResult.PASSED, TestResult.EXCEPTION,
                TestResult.NOT_TESTED, TestResult.PASSED, TestResult.PASSED,
                TestResult.EXCEPTION, TestResult.NOT_APPLICABLE, TestResult.PASSED,
            ]
            for i, wp in enumerate(wps.work_papers):
                wp.test_result = _DEMO_RESULTS[i % len(_DEMO_RESULTS)]
                if wp.test_result == TestResult.EXCEPTION and not wp.findings:
                    wp.add_finding(
                        title="Demo finding — control not fully implemented",
                        condition="Control activity is partially configured.",
                        criteria="ISO 27001 requires full implementation of this control.",
                        cause="Resource constraints delayed implementation.",
                        effect="Residual risk remains above accepted threshold.",
                        recommendation="Complete implementation by Q1 and re-test.",
                        severity=FindingSeverity.MEDIUM,
                        status=FindingStatus.OPEN,
                        owner="IT Security",
                        target_remediation_date="2027-03-31",
                    )
            args = argparse.Namespace(**{**vars(args), "_demo_wps": wps})
    else:
        results = []
        if args.results_dir:
            results = _load_results_from_dir(args.results_dir)
            print(f"Loaded {len(results)} analyses from {args.results_dir}")
        register = []
        if args.register:
            register = load_register(args.register)
            print(f"Loaded {len(register)} register entries from {args.register}")

    wps = getattr(args, "_demo_wps", None)
    if wps is not None and not (args.workpapers and args.workpapers.exists()):
        print(f"  Auto-generated {len(wps.work_papers)} demo working papers (ISO 27001)")
    if args.workpapers and args.workpapers.exists():
        from .audit_intelligence.workpaper import WorkPaperSet
        wps = WorkPaperSet.from_json(args.workpapers.read_text())
        print(f"Loaded {len(wps.work_papers)} working papers from {args.workpapers}")
    elif getattr(args, "wp_org", None) and args.wp_org != "Unknown Organisation":
        from .audit_intelligence.audit_universe import build_universe
        from .audit_intelligence.workpaper import build_workpapers
        scope = [s.strip() for s in args.wp_scope.split(",") if s.strip()]
        universe = build_universe(organisation=args.wp_org, scope=scope)
        wps = build_workpapers(universe)
        print(f"Generated {len(wps.work_papers)} working papers for '{args.wp_org}'")

    create_app(results, register, customer_name=args.customer, work_paper_set=wps)

    # Render.com injects PORT via environment; fall back to --port for local dev.
    actual_port = int(os.environ.get("PORT", args.port))
    print(f"\nDashboard: http://{args.host}:{actual_port}/\n")
    uvicorn.run(app, host=args.host, port=actual_port, log_level="warning")


# ---------------------------------------------------------------------------
# Module-level demo init (for direct uvicorn invocation: uvicorn ... :app)
# When DEMO_MODE=true, load synthetic data without requiring CLI args.
# ---------------------------------------------------------------------------

if os.environ.get("DEMO_MODE"):
    _demo_results, _demo_register = _load_demo_results()
    from .audit_intelligence.audit_universe import build_universe
    from .audit_intelligence.workpaper import (
        build_workpapers, TestResult, FindingSeverity, FindingStatus,
    )
    _demo_universe = build_universe(organisation="Demo Customer A/S", scope=["iso27001"])
    _demo_wps = build_workpapers(_demo_universe)
    _DEMO_TR = [
        TestResult.PASSED, TestResult.PASSED, TestResult.PASSED,
        TestResult.EXCEPTION, TestResult.PASSED, TestResult.EXCEPTION,
        TestResult.NOT_TESTED, TestResult.PASSED, TestResult.PASSED,
        TestResult.EXCEPTION, TestResult.NOT_APPLICABLE, TestResult.PASSED,
    ]
    for _di, _dwp in enumerate(_demo_wps.work_papers):
        _dwp.test_result = _DEMO_TR[_di % len(_DEMO_TR)]
        if _dwp.test_result == TestResult.EXCEPTION and not _dwp.findings:
            _dwp.add_finding(
                title="Demo finding — control not fully implemented",
                condition="Control activity is partially configured.",
                criteria="ISO 27001 requires full implementation of this control.",
                cause="Resource constraints delayed implementation.",
                effect="Residual risk remains above accepted threshold.",
                recommendation="Complete implementation by Q1 and re-test.",
                severity=FindingSeverity.MEDIUM,
                status=FindingStatus.OPEN,
                owner="IT Security",
                target_remediation_date="2027-03-31",
            )
    create_app(_demo_results, _demo_register, customer_name="Demo Customer", work_paper_set=_demo_wps)

if __name__ == "__main__":
    main()
