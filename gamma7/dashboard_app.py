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
    def _top_gaps(r):
        assessments = getattr(r, "assessments", []) or []
        missing = [a for a in assessments if a.get("status") in ("missing", "ambiguous")]
        return [
            {
                "req_id": a.get("requirement_article") or a.get("requirement_id", ""),
                "summary": a.get("requirement_summary", ""),
                "risk": a.get("risk_level", ""),
            }
            for a in missing[:4]
        ]
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
            "top_gaps": _top_gaps(r),
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
    return HTMLResponse(
        content=_DASHBOARD_HTML,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )


# ---------------------------------------------------------------------------
# Dashboard HTML (self-contained, no build step)
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gamma7 — Supplier Assurance</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #09090f;
    --surface: #111118;
    --surface2: #1a1a24;
    --border: #25253a;
    --border-hover: #35355a;
    --text: #e2e8f0;
    --secondary: #94a3b8;
    --muted: #64748b;
    --accent: #7c3aed;
    --green: #4ade80;
    --yellow: #eab308;
    --red: #f87171;
    --orange: #fb923c;
    --critical: #c084fc;
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    font-size: 14px;
    line-height: 1.5;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); min-height: 100vh; }

  .app-shell { display: flex; min-height: 100vh; }
  .sidebar {
    width: 220px; min-width: 220px; height: 100vh; position: fixed; top: 0; left: 0;
    background: var(--surface); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; z-index: 10; overflow-y: auto;
  }
  .sidebar-brand { padding: 20px 20px 16px; border-bottom: 1px solid var(--border); flex-shrink: 0; }
  .sidebar-brand .logo { font-weight: 700; font-size: 15px; letter-spacing: 0.06em; color: var(--accent); }
  .sidebar-brand .logo-sub { font-size: 11px; color: var(--muted); margin-top: 2px; }
  .sidebar-nav { padding: 10px 8px; flex: 1; }
  .nav-item {
    display: flex; align-items: center; padding: 8px 12px; border-radius: 6px;
    font-size: 13px; color: var(--secondary); cursor: pointer; border: none; background: none;
    width: 100%; text-align: left; border-left: 2px solid transparent;
    transition: color 0.12s, background 0.12s, border-color 0.12s; margin-bottom: 2px;
  }
  .nav-item:hover { color: var(--text); background: rgba(255,255,255,0.05); }
  .nav-item.active { color: var(--text); border-left-color: var(--accent); background: rgba(124,58,237,0.12); }
  /* per-section accent colours */
  .nav-overview.active    { color: #7c3aed; border-left-color: #7c3aed; background: rgba(124,58,237,0.06); }
  .nav-contracts.active   { color: #2563eb; border-left-color: #2563eb; background: rgba(37,99,235,0.06); }
  .nav-register.active    { color: #0d9488; border-left-color: #0d9488; background: rgba(13,148,136,0.06); }
  .nav-reconciliation.active { color: #d97706; border-left-color: #d97706; background: rgba(217,119,6,0.06); }
  .nav-workpapers.active  { color: #16a34a; border-left-color: #16a34a; background: rgba(22,163,74,0.06); }
  .nav-audit.active       { color: #e11d48; border-left-color: #e11d48; background: rgba(225,29,72,0.06); }
  .nav-boardreport.active { color: #6366f1; border-left-color: #6366f1; background: rgba(99,102,241,0.06); }
  /* section colour dots shown even when inactive */
  .nav-overview::before    { content:''; display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:8px; background:#7c3aed; opacity:0.5; flex-shrink:0; }
  .nav-contracts::before   { content:''; display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:8px; background:#2563eb; opacity:0.5; flex-shrink:0; }
  .nav-register::before    { content:''; display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:8px; background:#0d9488; opacity:0.5; flex-shrink:0; }
  .nav-reconciliation::before { content:''; display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:8px; background:#d97706; opacity:0.5; flex-shrink:0; }
  .nav-workpapers::before  { content:''; display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:8px; background:#16a34a; opacity:0.5; flex-shrink:0; }
  .nav-audit::before       { content:''; display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:8px; background:#e11d48; opacity:0.5; flex-shrink:0; }
  .nav-boardreport::before { content:''; display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:8px; background:#6366f1; opacity:0.5; flex-shrink:0; }
  .nav-overview.active::before    { opacity:1; }
  .nav-contracts.active::before   { opacity:1; }
  .nav-register.active::before    { opacity:1; }
  .nav-reconciliation.active::before { opacity:1; }
  .nav-workpapers.active::before  { opacity:1; }
  .nav-audit.active::before       { opacity:1; }
  .nav-boardreport.active::before { opacity:1; }
  .sidebar-footer { padding: 12px 16px; border-top: 1px solid var(--border); flex-shrink: 0; }
  .advisory-badge { font-size: 10px; color: var(--orange); background: rgba(249,115,22,0.1); border: 1px solid rgba(249,115,22,0.25); padding: 4px 8px; border-radius: 4px; line-height: 1.4; }
  .content-area { margin-left: 220px; flex: 1; min-width: 0; }
  main { padding: 28px 32px; max-width: 1180px; }

  .tab-pane { display: none; }
  .tab-pane.active { display: block; }

  .summary-cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
  .card {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 12px; padding: 18px 20px;
    transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
  }
  .card:hover { transform: translateY(-1px); border-color: var(--border-hover); }
  .card .label { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 500; }
  .card .value { font-size: 32px; font-weight: 700; margin-top: 6px; }
  .card .value.green { color: var(--green); }
  .card .value.yellow { color: var(--yellow); }
  .card .value.red { color: var(--red); }

  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 10px 12px; border-bottom: 1px solid var(--border);
    color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 500; }
  td { padding: 10px 12px; border-bottom: 1px solid var(--surface2); vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: rgba(255,255,255,0.025); cursor: pointer; }
  .table-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; overflow: hidden; }

  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 500; letter-spacing: 0.03em;
  }
  .badge-critical { background: #1c0a0a; color: #f87171; border: 1px solid rgba(239,68,68,0.3); }
  .badge-non-critical { background: #052e16; color: #4ade80; border: 1px solid rgba(34,197,94,0.25); }
  .badge-gaps { background: #1c0f00; color: #fb923c; border: 1px solid rgba(249,115,22,0.3); }
  .badge-compliant { background: #052e16; color: #4ade80; border: 1px solid rgba(34,197,94,0.25); }
  .badge-matched { background: #1e1b4b; color: #a5b4fc; border: 1px solid rgba(139,92,246,0.3); }
  .badge-missing { background: #1c0f00; color: #fb923c; border: 1px solid rgba(249,115,22,0.3); }

  .detail-panel {
    display: none; background: var(--surface2); border: 1px solid var(--border);
    border-radius: 12px; padding: 20px; margin-top: 16px;
  }
  .detail-panel.open { display: block; }
  .detail-panel h3 { margin-bottom: 12px; font-size: 15px; }
  .detail-panel pre {
    background: var(--bg); border: 1px solid var(--border); border-radius: 6px;
    padding: 14px; overflow-x: auto; font-size: 12px; color: var(--muted); white-space: pre-wrap;
    font-family: 'JetBrains Mono', monospace;
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
  .finding-row { margin-bottom: 16px; padding: 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; }
  .finding-row h4 { margin-bottom: 8px; font-size: 13px; }
  .finding-meta { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 10px; }
  .five-c { font-size: 12px; color: var(--muted); line-height: 1.6; }
  .five-c strong { color: var(--text); }
  .btn { background: #7c3aed; color: white; border: none; padding: 8px 14px; border-radius: 6px; cursor: pointer; font-size: 13px; text-decoration: none; display: inline-block; }
  .btn:hover { background: #6d28d9; }

  /* Tooltip */
  .tooltip-wrap { position: relative; display: inline-flex; align-items: center; gap: 4px; cursor: default; }
  .tooltip-icon { color: var(--muted); font-size: 11px; cursor: help; }
  .tooltip-box {
    display: none; position: absolute; top: 100%; left: 50%; transform: translateX(-50%);
    background: var(--surface2); border: 1px solid var(--border); border-radius: 8px;
    padding: 8px 12px; font-size: 12px; color: var(--text); white-space: normal;
    width: 220px; z-index: 100; line-height: 1.5; margin-top: 4px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.6); font-weight: 400; text-transform: none; letter-spacing: 0;
  }
  .tooltip-wrap:hover .tooltip-box { display: block; }

  /* Control strength badges */
  .ctrl-effective { background: #052e16; color: #4ade80; }
  .ctrl-partial   { background: #1c1500; color: #eab308; }
  .ctrl-weak      { background: #1c0a0a; color: #f87171; }

  /* Residual risk badges */
  .res-high   { background: #1c0a0a; color: #f87171; font-weight: 700; }
  .res-medium { background: #1c0f00; color: #fb923c; }
  .res-low    { background: #052e16; color: #4ade80; }

  /* Tab content header */
  .tab-header { margin-bottom: 20px; }
  .tab-header h2 { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
  .tab-header p  { font-size: 12px; color: var(--muted); line-height: 1.6; }

  /* Findings table inside detail panel */
  .findings-table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
  .findings-table th { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em; font-weight: 500; }
  .findings-table td { padding: 10px; border-bottom: 1px solid var(--surface2); vertical-align: top; }
  .findings-table tr:last-child td { border-bottom: none; }
  .status-missing   { color: var(--red); }
  .status-ambiguous { color: var(--yellow); }
  .status-satisfied { color: var(--green); }
  .status-na        { color: var(--muted); }

  /* Modal overlay */
  .modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.8); z-index: 200; overflow-y: auto; padding: 24px; align-items: flex-start; justify-content: center; }
  .modal-overlay.open { display: flex; }
  .modal-box { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 28px; max-width: 820px; width: 100%; position: relative; margin: auto; animation: modal-in 0.15s ease; }
  .modal-close { position: absolute; top: 14px; right: 18px; cursor: pointer; color: var(--muted); font-size: 20px; border: none; background: none; line-height: 1; }
  .modal-close:hover { color: var(--text); }
  @keyframes modal-in { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: scale(1); } }

  /* Legislation banners */
  .leg-banner { display: inline-block; padding: 6px 14px 6px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; margin-bottom: 14px; margin-right: 6px; }
  .leg-dora { background: #1e1b4b; color: #a5b4fc; border-left: 3px solid #8b5cf6; }
  .leg-gdpr { background: #052e16; color: #86efac; border-left: 3px solid #22c55e; }

  /* Consequence column */
  .consequence-cell { font-size: 11px; color: var(--orange); font-style: italic; line-height: 1.4; max-width: 180px; }

  /* Board Report */
  .board-section { margin-bottom: 28px; }
  .board-section-title { font-size: 14px; font-weight: 600; margin-bottom: 12px; color: var(--accent); border-bottom: 1px solid var(--border); padding-bottom: 8px; }
  .finding-compact { padding: 12px 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; }
  .finding-compact-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
  .audit-report-row { padding: 10px 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 6px; display: flex; justify-content: space-between; align-items: center; gap: 12px; }

  /* Working paper text */
  .wp-text-block { font-size: 13px; line-height: 1.8; padding: 14px 16px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; color: var(--text); margin-bottom: 14px; }
  .wp-declaration-label { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin-bottom: 6px; }
  .result-effective { color: var(--green); font-weight: 600; }
  .result-partial { color: var(--orange); font-weight: 600; }
  .result-not-effective { color: var(--red); font-weight: 600; }
  .result-residual-low { color: var(--green); }
  .result-residual-medium { color: var(--yellow); }
  .result-residual-na { color: var(--muted); }
  .result-not-tested { color: var(--muted); }
  .result-passed { color: var(--green); font-weight: 600; }
  .result-exception { color: var(--orange); font-weight: 600; }
  .result-na { color: var(--muted); }
  .info-box { background: rgba(139,92,246,0.08); border: 1px solid rgba(139,92,246,0.25); border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; font-size: 13px; line-height: 1.7; }
  .info-box strong { color: var(--accent); }
  .def-table { width: 100%; border-collapse: collapse; margin: 10px 0 0; font-size: 13px; }
  .def-table td { padding: 5px 8px; vertical-align: top; }
  .def-table td.term { font-weight: 600; white-space: nowrap; color: var(--accent); width: 1%; padding-right: 14px; }
  .def-table td.def { color: var(--text); line-height: 1.6; }
  .agent-label { font-size: 11px; text-transform: uppercase; letter-spacing: .07em; color: var(--accent); font-weight: 600; margin: 4px 0 10px; opacity: 0.85; }
  .proc-accordion { margin-bottom: 20px; }
  .proc-header { cursor: pointer; background: var(--surface2); border: 1px solid var(--border); border-radius: 6px; padding: 10px 14px; display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; user-select: none; }
  .proc-header:hover { background: var(--border); }
  .proc-body { display: none; padding: 10px 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 0 0 6px 6px; margin-top: -4px; margin-bottom: 8px; }
  .proc-body.open { display: block; }
  .sub-proc-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 12px; border-bottom: 1px solid var(--surface2); }
  .sub-proc-row:last-child { border-bottom: none; }
  .ctrl-badge { font-size: 10px; padding: 1px 6px; border-radius: 3px; font-family: 'JetBrains Mono', monospace; }
  .ctrl-badge-mapped { background: rgba(139,92,246,0.15); color: var(--accent); }
  .ctrl-badge-passed { background: rgba(34,197,94,0.15); color: var(--green); }
  .ctrl-badge-exception { background: rgba(249,115,22,0.15); color: var(--orange); }
  .ctrl-badge-not-tested { background: var(--surface2); color: var(--muted); }
  .badge-na { background: var(--surface2); color: var(--muted); }
  .lfap-tabs { display: flex; gap: 4px; margin-bottom: 14px; flex-wrap: wrap; }
  .lfap-tab { padding: 5px 12px; border-radius: 4px; font-size: 12px; cursor: pointer; background: var(--surface2); border: 1px solid var(--border); color: var(--muted); }
  .lfap-tab.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  .dar-modal-content { padding: 4px 0; }
  /* Custom scrollbar */
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--border-hover); border-radius: 2px; }

  /* ── Enhanced interactivity ───────────────────────────────── */
  .card {
    box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
  }
  .card:hover { transform: translateY(-2px); box-shadow: 0 6px 20px rgba(0,0,0,0.09); border-color: var(--border-hover); }
  .nav-item { transition: color 0.15s, background 0.15s, border-color 0.15s; }
  .table-wrap { box-shadow: 0 1px 4px rgba(0,0,0,0.05); }
  tr:hover td { background: rgba(255,255,255,0.03); }
  .modal-box { box-shadow: 0 20px 60px rgba(0,0,0,0.18); }
  button, [role=button] { transition: background 0.15s, color 0.15s, transform 0.1s, box-shadow 0.15s; }
  .tab-pane.active { animation: fade-in 0.18s ease; }
  @keyframes fade-in { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: translateY(0); } }
  .sidebar-brand .logo { letter-spacing: 0.08em; }

  /* ── Responsive ───────────────────────────────────────────── */
  @media (max-width: 1024px) {
    .sidebar { width: 180px; min-width: 180px; }
    .content-area { margin-left: 180px; }
    main { padding: 20px 24px; }
  }
  @media (max-width: 768px) {
    .sidebar {
      width: 100%; min-width: 0; height: auto;
      flex-direction: row; top: 0; left: 0;
      border-right: none; border-bottom: 1px solid var(--border);
      overflow-x: auto;
    }
    .sidebar-brand { padding: 10px 14px; border-bottom: none; border-right: 1px solid var(--border); flex-shrink: 0; }
    .sidebar-brand .logo-sub { display: none; }
    .sidebar-nav { flex-direction: row; padding: 0; overflow-x: auto; scrollbar-width: none; }
    .sidebar-nav::-webkit-scrollbar { display: none; }
    .nav-item {
      white-space: nowrap; padding: 12px 14px; border-left: none;
      border-bottom: 2px solid transparent; border-radius: 0;
    }
    .nav-item:hover { background: rgba(255,255,255,0.05); }
    .nav-overview.active    { border-bottom-color: #7c3aed; border-left-color: transparent; }
    .nav-contracts.active   { border-bottom-color: #2563eb; border-left-color: transparent; }
    .nav-register.active    { border-bottom-color: #0d9488; border-left-color: transparent; }
    .nav-reconciliation.active { border-bottom-color: #d97706; border-left-color: transparent; }
    .nav-workpapers.active  { border-bottom-color: #16a34a; border-left-color: transparent; }
    .nav-audit.active       { border-bottom-color: #e11d48; border-left-color: transparent; }
    .nav-boardreport.active { border-bottom-color: #6366f1; border-left-color: transparent; }
    .sidebar-footer { display: none; }
    .content-area { margin-left: 0; margin-top: 48px; }
    main { padding: 16px; }
    .summary-cards { grid-template-columns: 1fr 1fr; }
  }
  @media (max-width: 480px) {
    .summary-cards { grid-template-columns: 1fr; }
    .nav-item::before { display: none; }
  }
</style>
</head>
<body>
<div class="app-shell">
<aside class="sidebar">
  <div class="sidebar-brand">
    <div class="logo">GAMMA7</div>
    <div class="logo-sub">Audit &amp; Compliance</div>
  </div>
  <nav class="sidebar-nav">
    <button class="nav-item nav-overview active" onclick="showTab('overview',this)">Overview</button>
    <button class="nav-item nav-contracts" onclick="showTab('contracts',this)">Contracts</button>
    <button class="nav-item nav-register" onclick="showTab('register',this)">Register</button>
    <button class="nav-item nav-reconciliation" onclick="showTab('reconciliation',this)">ICT Register</button>
    <button class="nav-item nav-workpapers" onclick="showTab('workpapers',this)">Audit Papers</button>
    <button class="nav-item nav-audit" onclick="showTab('audit',this)">Audit Plan</button>
    <button class="nav-item nav-boardreport" onclick="showTab('boardreport',this)">Board Report</button>
    <button class="nav-item nav-protocols" onclick="showTab('protocols',this)" style="color:#0ea5e9;border-left:3px solid #0ea5e9">Protocols</button>
  </nav>
  <div class="sidebar-footer">
    <div class="lang-toggle" style="display:flex;gap:6px;justify-content:center;margin-bottom:8px">
      <button id="lang-en" onclick="setLang('en')" style="background:var(--accent);color:#fff;border:none;border-radius:12px;padding:3px 12px;font-size:11px;cursor:pointer;font-weight:600">EN</button>
      <button id="lang-da" onclick="setLang('da')" style="background:var(--surface2);color:var(--muted);border:1px solid var(--border);border-radius:12px;padding:3px 12px;font-size:11px;cursor:pointer">DA</button>
    </div>
    <div class="advisory-badge" data-i18n="advisory">&#9888; Advisory — Human review required before regulatory use</div>
  </div>
</aside>
<div class="content-area">
<main>
  <!-- Overview tab -->
  <div id="tab-overview" class="tab-pane active">
    <div class="summary-cards" id="summary-cards" style="grid-template-columns:repeat(5,1fr)">
      <div class="card" style="border-top:3px solid var(--red)">
        <div class="label">P1 — Critical Findings</div>
        <div class="value red" id="stat-p1">&#8212;</div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px">Open, unresolved</div>
      </div>
      <div class="card" style="border-top:3px solid var(--orange)">
        <div class="label">P2 — Major Findings</div>
        <div class="value" style="color:var(--orange)" id="stat-p2">&#8212;</div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px">Open, unresolved</div>
      </div>
      <div class="card" style="border-top:3px solid var(--yellow)">
        <div class="label">P3 — Medium Findings</div>
        <div class="value yellow" id="stat-p3">&#8212;</div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px">Open, unresolved</div>
      </div>
      <div class="card" style="border-top:3px solid var(--red)">
        <div class="label"><span class="tooltip-wrap">Critical Contract Gaps<span class="tooltip-icon">ⓘ</span><span class="tooltip-box" style="width:240px">Contracts (Critical/Important classification) with 6 or more missing DORA Art. 30 / GDPR Art. 28 requirements — Weak Controls.</span></span></div>
        <div class="value red" id="stat-critical-gaps">&#8212;</div>
        <div style="font-size:11px;color:var(--muted);margin-top:4px">Require immediate attention</div>
      </div>
      <div class="card" style="border-top:3px solid var(--accent)">
        <div class="label">Next Audit Activity</div>
        <div class="value" style="font-size:14px;line-height:1.3" id="stat-next-audit">&#8212;</div>
      </div>
    </div>
    <div id="load-status" style="font-size:12px;color:var(--muted);margin-bottom:6px;padding:4px 8px;background:var(--surface-1);border-radius:4px;display:none"></div>
    <div class="table-wrap" style="margin-top:20px">
      <table>
        <thead><tr>
          <th>Supplier</th>
          <th><span class="tooltip-wrap">Criticality<span class="tooltip-icon">ⓘ</span><span class="tooltip-box"><strong>Critical/Important:</strong> ICT function critical to core financial processes.<br><br><strong>Non-critical:</strong> Supporting function.</span></span></th>
          <th><span class="tooltip-wrap">Open Findings<span class="tooltip-icon">ⓘ</span><span class="tooltip-box">P1 = Critical · P2 = Major · P3 = Minor</span></span></th>
          <th><span class="tooltip-wrap">Contract Gaps<span class="tooltip-icon">ⓘ</span><span class="tooltip-box">Missing DORA Art. 30 / GDPR Art. 28 requirements. Click a row for detail.</span></span></th>
          <th>Status</th>
        </tr></thead>
        <tbody id="overview-table"><tr><td colspan="5" class="empty">Loading&#8230;</td></tr></tbody>
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
          <th>Supplier</th>
          <th><span class="tooltip-wrap">Criticality<span class="tooltip-icon">ⓘ</span><span class="tooltip-box"><strong>Critical/Important:</strong> ICT function critical to core financial processes — stricter DORA Art. 30 requirements apply.<br><br><strong>Non-critical:</strong> Supporting function.</span></span></th>
          <th><span class="tooltip-wrap">Gaps<span class="tooltip-icon">ⓘ</span><span class="tooltip-box">Number of missing DORA Art. 30 / GDPR Art. 28 requirements. Click a row to view the full gap list.</span></span></th>
          <th><span class="tooltip-wrap">Top Consequence<span class="tooltip-icon">ⓘ</span><span class="tooltip-box">Business risk consequence of the most critical missing clause.</span></span></th>
          <th>Status</th>
        </tr></thead>
        <tbody id="contracts-table"><tr><td colspan="5" class="empty">Loading&#8230;</td></tr></tbody>
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
      <h2>ICT Register Reconciliation</h2>
      <div class="agent-label">DORA Art. 28(3) — ICT Third-Party Register Completeness Check</div>
      <table class="def-table">
        <tr><td class="term">What is this?</td><td class="def">DORA Art. 28(3) requires financial entities to maintain a complete, accurate register of all ICT third-party service providers. This tab cross-checks the ICT Register against contract analyses performed to date.</td></tr>
        <tr><td class="term"><span style="color:var(--orange)">&#9888; Register Only</span></td><td class="def">Provider is in the ICT Register but has no contract analysis. DORA requires assessment of all ICT contracts before renewal — this is an open compliance gap.</td></tr>
        <tr><td class="term"><span style="color:var(--yellow)">&#9888; Contract Only</span></td><td class="def">A contract was analysed but the supplier is not in the register. The register is incomplete and must be updated.</td></tr>
        <tr><td class="term">Supervisory readiness</td><td class="def">You must demonstrate (a) the register is complete and (b) every critical/important provider has been assessed. Click any row to view the analysis or register entry.</td></tr>
      </table>
    </div>
    <div id="recon-content"><div class="loading">Loading reconciliation&#8230;</div></div>
  </div>

  <!-- Working Papers tab (Agent 2) -->
  <div id="tab-workpapers" class="tab-pane">
    <div class="tab-header">
      <h2>Audit Papers</h2>
      <div class="agent-label">DORA Art. 30 ICT Third-Party Contract Testing</div>
    </div>
    <div id="wp-content"></div>
  </div>

  <!-- Audit Intelligence tab (Agent 4) -->
  <div id="tab-audit" class="tab-pane">
    <div class="tab-header">
      <h2>Audit Plan</h2>
      <div class="agent-label">Internal Audit &amp; Board Reporting — ISO 27001 / ISAE 3402 / IIA Standards</div>
      <table class="def-table" style="margin-top:10px">
        <tr><td class="term">Audit Universe</td><td class="def">Complete inventory of controls, processes and systems subject to this engagement.</td></tr>
        <tr><td class="term">Risk Matrix</td><td class="def">3×3 Likelihood × Impact (1–9). Heatmap shows inherent risk × test result.</td></tr>
        <tr><td class="term">Draft Audit Report</td><td class="def">Working papers support each DAR. Click a report card to view scope and findings summary.</td></tr>
        <tr><td class="term">Agent 2 vs Agent 4</td><td class="def">Agent 2 covers ICT supplier assurance (DORA Art. 30 contract testing). Agent 4 covers broader internal audit, ISO 27001 control testing, and board reporting (ISAE 3402 / IIA).</td></tr>
      </table>
    </div>
    <div id="audit-content"></div>
  </div>

  <!-- Board Report tab -->
  <div id="tab-boardreport" class="tab-pane">
    <div class="tab-header">
      <h2>Board Report</h2>
      <div class="agent-label">Agent 4 — Internal Audit &amp; Board Reporting</div>
      <div class="agent-label" style="margin-top:4px">ISO 27001 / ISAE 3402 — Board &amp; Governance Reporting</div>
      <table class="def-table">
        <tr><td class="term">Open / Closed Findings</td><td class="def">Current status of all findings from the period under review.</td></tr>
        <tr><td class="term">Finalised Reports</td><td class="def">Board-presented audit reports with follow-up obligations.</td></tr>
        <tr><td class="term">Prior Period (LFAP)</td><td class="def">Log of Follow-up Actions from prior period finalised reports — tracked by quarter.</td></tr>
        <tr><td class="term">Board Intelligence</td><td class="def">Value tracker — control improvements, risk reductions, and compliance milestones surfaced this period.</td></tr>
        <tr><td class="term">Next Quarter Focus</td><td class="def">Internal audit plan priorities for the upcoming quarter. <strong style="color:var(--orange)">Human review required</strong> before distributing to governance bodies.</td></tr>
      </table>
    </div>
    <div id="boardreport-content"></div>
  </div>

  <div id="tab-protocols" class="tab-pane">
    <div class="tab-header">
      <h2 style="color:#0ea5e9">Protokoller</h2>
      <div class="agent-label">Eksempler p&#229; udfyldte revisionsprotokol-skabeloner — Gamma7 Finans A/S</div>
    </div>
    <div style="max-width:960px;margin:0 auto">

      <!-- Protocol 1: Quarterly Q3 -->
      <div style="background:var(--surface2);border-radius:8px;padding:20px 22px;margin-bottom:28px;border:1px solid var(--border)">
        <div style="font-size:11px;font-weight:700;color:var(--accent);text-transform:uppercase;letter-spacing:.08em;margin-bottom:16px">Protokol 1 &#8212; Kvartalsmæssig intern systemrevision (Q3 2025)</div>

        <table class="def-table" style="margin-bottom:20px">
          <tr><td class="term">Virksomhed</td><td class="def">Gamma7 Finans A/S</td></tr>
          <tr><td class="term">Systemer d&#230;kket</td><td class="def">Core Banking System (CBS), Azure AD / Entra ID, Tenable.io</td></tr>
          <tr><td class="term">Revisionsperiode</td><td class="def">1. juli &#8211; 30. september 2025 (Q3 2025)</td></tr>
          <tr><td class="term">Dato for protokol</td><td class="def">15. oktober 2025</td></tr>
          <tr><td class="term">Udarbejdet af</td><td class="def">Intern Revision, Gamma7 Finans A/S</td></tr>
          <tr><td class="term">Modtagere</td><td class="def">Bestyrelsens Revisions- og Risikoudvalg (BRRU) &#183; Administrerende direkt&#248;r &#183; CTO</td></tr>
        </table>

        <div style="font-size:13px;font-weight:700;color:var(--text);margin:16px 0 6px">1. Kvartalets fokusomr&#229;der</div>
        <p style="font-size:13px;line-height:1.7;margin:0 0 12px">Q3 2025 fokuserede p&#229; informationssikkerhed og adgangskontrol jf. den risikobaserede revisionsplan for 2025. F&#248;lgende kontroller var planlagt og gennemf&#248;rt: identitets- og adgangsstyring (AU01&#8211;AU02) samt s&#229;rbarhedsst&#229;ndtering (AU04&#8211;AU05). Business Continuity Planning (AU07) blev p&#229;begyndt og afsluttes i Q4.</p>

        <div style="font-size:13px;font-weight:700;color:var(--text);margin:16px 0 6px">2. Samlet kvartalsvurdering</div>
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
          <span style="width:14px;height:14px;border-radius:50%;background:var(--orange);display:inline-block;flex-shrink:0"></span>
          <span style="font-size:13px;font-weight:600;color:var(--orange)">GUL &#8212; Tilfredsstillende med bem&#230;rkninger</span>
        </div>
        <p style="font-size:13px;line-height:1.7;margin:0 0 12px">Revisionen af adgangskontroller (AU01&#8211;AU02) konstaterede &#233;n afvigelse i den kvartalsvise adgangsgennemgang, som ikke var gennemf&#248;rt rettidigt. S&#229;rbarhedsst&#229;ndteringen (AU04&#8211;AU05) viste, at 3 kritiske CVE&#8217;er var patchet efter SLA-fristen. Begge afvigelser er eskaleret til IT-sikkerhedschefen med aftalte afhj&#230;lpningsplaner.</p>

        <div style="font-size:13px;font-weight:700;color:var(--text);margin:16px 0 10px">3. Gennemf&#248;rte kontroller og observationer</div>
        <div class="table-wrap"><table style="font-size:12px">
          <thead><tr>
            <th>Kontrolomr&#229;de</th>
            <th>Gennemf&#248;rt test</th>
            <th>Resultat og observation</th>
            <th>Alvorlighed</th>
            <th>Status / Handling</th>
          </tr></thead>
          <tbody>
            <tr>
              <td style="font-size:12px;font-weight:600">Adgangskontrol &#8212; kvartalsmæssig gennemgang<br><code style="font-size:10px">AU01</code></td>
              <td style="font-size:12px">Gennemgang af adgangsrettigheder for 42 brugere i Azure AD; kontrol af dokumentation for Q3-gennemgang</td>
              <td style="font-size:12px">Q3-gennemgangen for 7 privilegerede konti var ikke gennemf&#248;rt inden fristens udl&#248;b (30. sept.). Ingen uautoriserede adgange identificeret.</td>
              <td><span style="background:rgba(251,146,60,0.2);color:var(--orange);border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600">MIDDEL</span></td>
              <td style="font-size:12px;color:var(--orange)">&#197;ben &#8212; IT-sikkerhedschef ansvarlig; afhj&#230;lpningsfrist 31. okt. 2025</td>
            </tr>
            <tr>
              <td style="font-size:12px;font-weight:600">Privilegeret adgangsstyring (PAM)<br><code style="font-size:10px">AU02</code></td>
              <td style="font-size:12px">Test af CyberArk session recordings for 15 privilegerede sessioner; verifikation af godkendelsesworkflow</td>
              <td style="font-size:12px">Alle 15 sessioner var korrekt logget og godkendt. Ingen afvigelser.</td>
              <td><span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600">GODKENDT</span></td>
              <td style="font-size:12px;color:var(--green)">Lukket &#8212; ingen handling p&#229;kr&#230;vet</td>
            </tr>
            <tr>
              <td style="font-size:12px;font-weight:600">Patch- og s&#229;rbarhedsst&#229;ndtering<br><code style="font-size:10px">AU04</code></td>
              <td style="font-size:12px">Udtræk af &#229;bne CVE&#8217;er fra Tenable.io; verifikation af patch-SLA-overholdelse (kritisk: 72t, h&#248;j: 7d)</td>
              <td style="font-size:12px">3 kritiske CVE&#8217;er patchet 4&#8211;9 dage over SLA-fristen. Eskaleringsprocessen var ikke aktiveret.</td>
              <td><span style="background:rgba(248,113,113,0.2);color:var(--red);border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600">H&#216;J</span></td>
              <td style="font-size:12px;color:var(--orange)">&#197;ben &#8212; CISO ansvarlig; afhj&#230;lpningsfrist 15. nov. 2025; procesforbedring kr&#230;vet</td>
            </tr>
            <tr>
              <td style="font-size:12px;font-weight:600">Sikkerhedsovervågning (SIEM)<br><code style="font-size:10px">AU05</code></td>
              <td style="font-size:12px">Stikpr&#248;ve af 20 SIEM-alarmer; verifikation af klassifikation og responstid</td>
              <td style="font-size:12px">19 af 20 alarmer var korrekt klassificeret og behandlet inden for SLA. &#201;n alarm var fejlklassificeret som lav risiko.</td>
              <td><span style="background:rgba(251,191,36,0.2);color:var(--yellow);border-radius:4px;padding:2px 8px;font-size:11px;font-weight:600">LAV</span></td>
              <td style="font-size:12px;color:var(--green)">Lukket &#8212; klassifikationsguide opdateret</td>
            </tr>
          </tbody>
        </table></div>

        <div style="font-size:13px;font-weight:700;color:var(--text);margin:20px 0 10px">4. Underskrifter</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;font-size:12px">
          <div style="border-top:1px solid var(--border);padding-top:10px">
            <div style="color:var(--muted);margin-bottom:4px">Udarbejdet af &#8212; Intern Revision</div>
            <div style="height:32px"></div>
            <div style="color:var(--muted);font-size:11px">Dato: 15. oktober 2025</div>
          </div>
          <div style="border-top:1px solid var(--border);padding-top:10px">
            <div style="color:var(--muted);margin-bottom:4px">Godkendt af &#8212; Bestyrelsesformand, BRRU</div>
            <div style="height:32px"></div>
            <div style="color:var(--muted);font-size:11px">Dato: _______________</div>
          </div>
        </div>
      </div>

      <!-- Protocol 2: Annual 2026 -->
      <div style="background:var(--surface2);border-radius:8px;padding:20px 22px;margin-bottom:28px;border:1px solid var(--border)">
        <div style="font-size:11px;font-weight:700;color:#0ea5e9;text-transform:uppercase;letter-spacing:.08em;margin-bottom:16px">Protokol 2 &#8212; &#197;rsprotokol for intern systemrevision (2026)</div>

        <table class="def-table" style="margin-bottom:20px">
          <tr><td class="term">Virksomhed</td><td class="def">Gamma7 Finans A/S</td></tr>
          <tr><td class="term">Regnskabs&#229;r</td><td class="def">1. januar &#8211; 31. december 2026</td></tr>
          <tr><td class="term">Dato for protokol</td><td class="def">15. januar 2027</td></tr>
          <tr><td class="term">Udarbejdet af</td><td class="def">Chief Audit Executive (CAE), Gamma7 Finans A/S</td></tr>
          <tr><td class="term">Rapporteret til</td><td class="def">Bestyrelsens Revisions- og Risikoudvalg (BRRU) &#183; Bestyrelsen</td></tr>
        </table>

        <div style="font-size:13px;font-weight:700;color:var(--text);margin:16px 0 6px">1. Form&#229;l og gennemf&#248;relse af &#229;rsplan</div>
        <p style="font-size:13px;line-height:1.7;margin:0 0 12px">&#197;rsplanen for 2026 er udarbejdet i henhold til den risikobaserede 3-&#229;rige revisionsmetode vedtaget af BRRU. Planen d&#230;kkede revisionsunivers best&#229;ende af 10 kontroller fordelt p&#229; fire procesdomæner: Informationssikkerhed, ICT-tredjepartsrisiko, Operationel robusthed og Databeskyttelse (GDPR). I alt er 8 planlagte revisioner gennemf&#248;rt; 2 revisioner er udsat til Q1 2027 grundet ressourcebegr&#230;nsninger, som BRRU er orienteret om.</p>

        <div style="font-size:13px;font-weight:700;color:var(--text);margin:16px 0 6px">2. &#197;rskonklusion</div>
        <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
          <span style="width:14px;height:14px;border-radius:50%;background:var(--orange);display:inline-block;flex-shrink:0"></span>
          <span style="font-size:13px;font-weight:600;color:var(--orange)">GUL &#8212; Tilfredsstillende med v&#230;sentlige bem&#230;rkninger</span>
        </div>
        <p style="font-size:13px;line-height:1.7;margin:0 0 12px">Revisionen for 2026 identificerede i alt 7 fund, heraf 2 h&#248;j-prioritet (P1), 3 medium-prioritet (P2) og 2 lav-prioritet (P3). Pr. 31. december 2026 er 5 fund lukket; 2 P1-fund vedr&#248;rende DORA Art. 30 kontraktmangler og adgangskontrol-gennemgang er fortsat &#229;bne med aftalte afhj&#230;lpningsplaner. CAE vurderer det interne kontrolmilj&#248; som tilfredsstillende med behov for forbedring inden for tredjepartsrisikostyring.</p>

        <div style="font-size:13px;font-weight:700;color:var(--text);margin:16px 0 10px">3. Oversigt over gennemf&#248;rte revisioner og n&#248;glefund i 2026</div>
        <div class="table-wrap"><table style="font-size:12px">
          <thead><tr>
            <th style="white-space:nowrap">Kvartal / Tema</th>
            <th>Revideret omr&#229;de</th>
            <th>Samlet vurdering</th>
            <th>N&#248;gleobservationer</th>
            <th style="white-space:nowrap">Status pr. 31.12</th>
          </tr></thead>
          <tbody>
            <tr>
              <td style="font-size:12px;font-weight:600;white-space:nowrap">Q1 2026<br>Adgangskontrol</td>
              <td style="font-size:12px">ISO 27001 A.9 &#8212; IAM &amp; PAM (AU01&#8211;AU02)</td>
              <td><span style="background:rgba(251,146,60,0.2);color:var(--orange);border-radius:4px;padding:2px 8px;font-size:11px">GUL</span></td>
              <td style="font-size:12px">Kvartalsmæssig adgangsgennemgang ikke gennemf&#248;rt for 7 konti (P1). PAM-kontroller godkendt.</td>
              <td><span style="background:rgba(251,191,36,0.2);color:var(--yellow);border-radius:4px;padding:2px 8px;font-size:11px">&#197;ben</span></td>
            </tr>
            <tr>
              <td style="font-size:12px;font-weight:600;white-space:nowrap">Q2 2026<br>DORA Art. 30</td>
              <td style="font-size:12px">DORA Art. 30 kontraktklausuler &#8212; 5 kritiske leverand&#248;rer</td>
              <td><span style="background:rgba(248,113,113,0.2);color:var(--red);border-radius:4px;padding:2px 8px;font-size:11px">R&#216;D</span></td>
              <td style="font-size:12px">Exitstrategi mangler for 2 kritiske leverand&#248;rer (P1). SLA-klausuler mangler i &#233;n kontrakt (P2).</td>
              <td><span style="background:rgba(251,191,36,0.2);color:var(--yellow);border-radius:4px;padding:2px 8px;font-size:11px">&#197;ben</span></td>
            </tr>
            <tr>
              <td style="font-size:12px;font-weight:600;white-space:nowrap">Q2 2026<br>Datakryptering</td>
              <td style="font-size:12px">ISO 27001 A.10 &#8212; Kryptering og n&#248;gleh&#229;ndtering (AU03)</td>
              <td><span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 8px;font-size:11px">GR&#216;N</span></td>
              <td style="font-size:12px">Alle systemer krypterer data-at-rest og data-in-transit med godkendte algoritmer. N&#248;glerotation gennemf&#248;rt rettidigt.</td>
              <td><span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 8px;font-size:11px">Lukket</span></td>
            </tr>
            <tr>
              <td style="font-size:12px;font-weight:600;white-space:nowrap">Q3 2026<br>S&#229;rbarhedsstyring</td>
              <td style="font-size:12px">ISO 27001 A.12.6 &#8212; Patch management (AU04&#8211;AU05)</td>
              <td><span style="background:rgba(251,146,60,0.2);color:var(--orange);border-radius:4px;padding:2px 8px;font-size:11px">GUL</span></td>
              <td style="font-size:12px">3 kritiske patches forsinket ud over SLA (P2). Eskaleringsproces implementeret efter Q3 2025-revisionen.</td>
              <td><span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 8px;font-size:11px">Lukket</span></td>
            </tr>
            <tr>
              <td style="font-size:12px;font-weight:600;white-space:nowrap">Q4 2026<br>BCP/DR</td>
              <td style="font-size:12px">ISO 27001 A.17 / DORA Art. 11 &#8212; Forretningskontinuitet (AU07)</td>
              <td><span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 8px;font-size:11px">GR&#216;N</span></td>
              <td style="font-size:12px">BCP-dokumentation opdateret og DR-test gennemf&#248;rt med tilfredsstillende resultat. RTO/RPO opfyldt.</td>
              <td><span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 8px;font-size:11px">Lukket</span></td>
            </tr>
          </tbody>
        </table></div>

        <div style="font-size:13px;font-weight:700;color:var(--text);margin:20px 0 6px">4. Fokusomr&#229;der for n&#230;ste &#229;rs revisionsplan (2027)</div>
        <ul style="font-size:13px;line-height:1.9;margin:0 0 12px;padding-left:20px">
          <li><strong>DORA Art. 30 follow-up:</strong> Verifikation af implementering af exitstrategier og manglende kontraktklausuler (&#229;bne P1-fund)</li>
          <li><strong>Adgangskontrol:</strong> Opf&#248;lgningsrevision af IAM-processer med fokus p&#229; automatisering af kvartalsmæssig adgangsgennemgang</li>
          <li><strong>GDPR Art. 28:</strong> Gennemgang af databehandleraftaler og underbehandlerdokumentation (planlagt i Q2 2027)</li>
          <li><strong>ICT-h&#230;ndelsesh&#229;ndtering:</strong> F&#248;rstegangsvurdering af DORA Art. 17 h&#230;ndelsesbeeredskab og rapporteringsprocedurer</li>
          <li><strong>Koncentrationsrisiko:</strong> DORA Art. 29 &#229;rlig vurdering af ICT-leverand&#248;rkoncentration</li>
        </ul>

        <div style="font-size:13px;font-weight:700;color:var(--text);margin:20px 0 10px">5. Underskrifter</div>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px;font-size:12px">
          <div style="border-top:1px solid var(--border);padding-top:10px">
            <div style="color:var(--muted);margin-bottom:4px">Chief Audit Executive</div>
            <div style="height:32px"></div>
            <div style="color:var(--muted);font-size:11px">Dato: 15. januar 2027</div>
          </div>
          <div style="border-top:1px solid var(--border);padding-top:10px">
            <div style="color:var(--muted);margin-bottom:4px">Bestyrelsesformand, BRRU</div>
            <div style="height:32px"></div>
            <div style="color:var(--muted);font-size:11px">Dato: _______________</div>
          </div>
          <div style="border-top:1px solid var(--border);padding-top:10px">
            <div style="color:var(--muted);margin-bottom:4px">Administrerende direkt&#248;r</div>
            <div style="height:32px"></div>
            <div style="color:var(--muted);font-size:11px">Dato: _______________</div>
          </div>
        </div>
      </div>

    </div>
  </div>
</main>
</div><!-- content-area -->
</div><!-- app-shell -->

<!-- Contract summary modal -->
<div id="contract-modal" class="modal-overlay" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="modal-box">
    <button class="modal-close" onclick="document.getElementById('contract-modal').classList.remove('open')">&#10005;</button>
    <div id="contract-modal-content"></div>
  </div>
</div>

<!-- Draft Audit Report modal -->
<div id="dar-modal" class="modal-overlay" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="modal-box" style="max-width:720px">
    <button class="modal-close" onclick="document.getElementById('dar-modal').classList.remove('open')">&#10005;</button>
    <div id="dar-modal-content" class="dar-modal-content"></div>
  </div>
</div>

<script>
let analyses = [];
let registerEntries = [];

function showTab(name, el) {
  document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  if (el) el.classList.add('active');
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
  'ART30-001': 'The contract does not restrict sub-contracting of critical ICT functions. As a result, core services may be delegated to undisclosed sub-processors without customer approval. This creates an uncontrolled chain of ICT dependency outside the financial entity\\'s oversight framework, in breach of DORA Art. 30(3).',
  'ART30-002': 'The contract does not specify permitted data locations. Customer data may be processed or stored in jurisdictions without an adequate level of protection, in violation of GDPR Chapter V and DORA Art. 30(2)(j). The financial entity cannot demonstrate lawful transfers to supervisory authorities.',
  'ART30-003': 'No minimum information security requirements are contractually defined. In the event of a data breach, the financial entity cannot rely on the contract to attribute fault or trigger liability. This gap undermines the ability to demonstrate adequate vendor oversight under DORA Art. 30(2)(d).',
  'ART30-004': 'The contract contains no enforceable service level targets. The financial entity cannot hold the supplier accountable for degraded performance or outages. Without SLAs, the operational resilience framework required by DORA Art. 30(2)(d) is not supported by contractual controls.',
  'ART30-005': 'The supplier has no contractual obligation to report ICT-related incidents. The financial entity may not receive timely notification, preventing it from meeting its own reporting obligations under DORA Art. 17 and Art. 19. Incident response timelines will be impaired.',
  'ART30-006': 'The contract does not grant audit or inspection rights over the supplier. The financial entity cannot verify DORA Art. 30 compliance through independent testing or reviews. This exposes it to supervisory scrutiny for failing to demonstrate adequate third-party oversight.',
  'ART30-007': 'No exit strategy or termination plan is documented. If the supplier fails or the relationship ends, the financial entity has no contractual basis to demand orderly transition of services or data. Operational continuity is at risk, contrary to DORA Art. 30(2)(g).',
  'ART30-008': 'The contract does not guarantee data portability on exit or termination. In the event of supplier failure, data retrieval may be delayed or blocked, increasing recovery time and regulatory exposure. This conflicts with DORA Art. 30(2)(h) continuity requirements.',
  'ART30-009': 'Business continuity obligations are not contractually specified. The supplier is not required to maintain or test resilience capabilities aligned with the financial entity\\'s own BCP/DR framework. This creates an unverifiable dependency contrary to DORA Art. 30(2)(e).',
  'ART30-010': 'The contract imposes no obligation to test business continuity or recovery plans. Supplier resilience is assumed but unverified. The financial entity cannot demonstrate to supervisors that ICT resilience across the supply chain has been tested, as required under DORA Art. 30(2)(f).',
  'ART30-011': 'No change management or advance notification requirements are contractually defined. The supplier may alter infrastructure, processes or sub-contractors without notifying the financial entity, introducing uncontrolled change risk in breach of DORA Art. 30(2)(i).',
  'ART30-012': 'The contract contains no capacity management provisions. The supplier is not contractually required to scale or maintain performance under demand increases, creating availability and performance risk during peak operational periods.',
  'ART30-013': 'No data processing agreement (DPA) is in place. This is a direct violation of GDPR Art. 28, which requires a written contract between the controller and processor. Supervisory authorities may impose fines of up to 2% of global annual turnover for this breach.',
};
const ARTICLE_CITATIONS = {
  'ART30-001': 'DORA Art. 30(3) — Sub-contracting of critical or important functions',
  'ART30-002': 'DORA Art. 30(2)(j) · GDPR Chapter V — Data location and transfer controls',
  'ART30-003': 'DORA Art. 30(2)(d) — Information security requirements',
  'ART30-004': 'DORA Art. 30(2)(d) — Service levels and performance standards',
  'ART30-005': 'DORA Art. 17 · Art. 19 — ICT-related incident reporting',
  'ART30-006': 'DORA Art. 30(2)(f) — Audit rights and inspection',
  'ART30-007': 'DORA Art. 30(2)(g) — Exit strategy and termination',
  'ART30-008': 'DORA Art. 30(2)(h) — Data portability on exit',
  'ART30-009': 'DORA Art. 30(2)(e) — Business continuity obligations',
  'ART30-010': 'DORA Art. 30(2)(f) — Testing of continuity and recovery plans',
  'ART30-011': 'DORA Art. 30(2)(i) — Change management and notification',
  'ART30-012': 'DORA Art. 30(2)(d) — Capacity and performance management',
  'ART30-013': 'GDPR Art. 28(3) — Data processing agreement (mandatory written contract)',
};
const REMEDIES = {
  'ART30-001': {text:'Amend the contract to include a sub-contracting approval clause requiring written consent before delegation of critical ICT functions.',effort:'Low'},
  'ART30-002': {text:'Specify permitted data processing locations in a contract schedule. Establish SCCs or adequacy decisions for non-EEA transfers.',effort:'Medium'},
  'ART30-003': {text:'Attach a minimum security requirements annex aligned to ISO 27001 or equivalent. Include audit rights for security controls.',effort:'Medium'},
  'ART30-004': {text:'Define measurable SLAs (availability, RTO, RPO) in a service schedule. Include remedies for SLA breach.',effort:'Low'},
  'ART30-005': {text:'Insert an incident notification clause requiring the supplier to report ICT incidents within 24 hours.',effort:'Low'},
  'ART30-006': {text:'Include a right-to-audit clause with at least annual inspection rights, including access to sub-processors.',effort:'Low'},
  'ART30-007': {text:'Develop and attach a formal exit strategy covering transition timelines, data handover, and knowledge transfer obligations.',effort:'High'},
  'ART30-008': {text:'Include a data portability clause guaranteeing extraction in a standard, machine-readable format within a specified timeframe.',effort:'Low'},
  'ART30-009': {text:'Require the supplier to maintain a BCP aligned with the financial entity\\'s own requirements and provide evidence on request.',effort:'Medium'},
  'ART30-010': {text:'Mandate annual BCP/DR testing and require the supplier to provide test results to the financial entity.',effort:'Medium'},
  'ART30-011': {text:'Include a change management clause requiring advance notice (minimum 30 days) for significant infrastructure or process changes.',effort:'Low'},
  'ART30-012': {text:'Define capacity thresholds and require the supplier to notify and address capacity constraints before they impact service.',effort:'Low'},
  'ART30-013': {text:'Execute a GDPR-compliant Data Processing Agreement including all mandatory Art. 28(3) elements immediately.',effort:'Low'},
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
  const satisfied = assessments.filter(x => x.status === 'satisfied');
  const cs = controlStrength(missing.length);
  const csClass = controlStrengthClass(missing.length);
  const rr = residualRisk(a.classification, missing.length);
  const rrEmoji = rr === 'High' ? '🔴' : rr === 'Medium' ? '🟠' : '🟢';
  const isDORA = assessments.some(x => (x.requirement_id||'').startsWith('ART30'));
  const isGDPR = assessments.some(x => (x.requirement_id||'').startsWith('ART28'));
  const legLine = [isDORA ? 'DORA Art. 30' : '', isGDPR ? 'GDPR Art. 28' : ''].filter(Boolean).join(' · ');

  // Service type context from register
  const reg = registerEntries.find(r => r.supplier_name && r.supplier_name.toLowerCase() === (a.supplier_name||'').toLowerCase());
  const svcMap = {
    'cloud_hosting': 'cloud infrastructure hosting critical banking data, managed databases and 24/7 monitoring',
    'payment_processing': 'payment processing supporting card authorisation, fraud detection, clearing and settlement',
    'colocation': 'co-location and mainframe hosting underpinning core batch processing operations',
    'saas_grc_platform': 'SaaS GRC platform for policy management, compliance and document repository (non-core function)',
    'document_management': 'secure digital document archiving, e-signing and regulatory reporting support',
    'network_connectivity': 'managed WAN/SD-WAN connectivity for all branch offices and data centres',
  };
  const svcLabel = reg ? (svcMap[reg.service_type] || (reg.service_type||'').replace(/_/g,' ')) : 'ICT service';
  const classNarrative = a.classification === 'critical_important'
    ? `The service is classified as <strong>Critical / Important</strong> because it provides ${svcLabel}. Under DORA Art. 2, any disruption would materially impact continuity of financial services. The highest level of contractual control applies — all 13 DORA Art. 30 requirements must be met.`
    : `The service is classified as <strong>Non-Critical</strong> because it provides ${svcLabel}, supporting operations but not directly tied to core financial processes. Standard contractual controls apply with proportionate obligations.`;
  const riskNarrative = rr === 'High'
    ? `Residual risk is 🔴 <strong>HIGH</strong> — a Critical/Important supplier with ${missing.length} contractual gap(s) represents a real probability of uncontrolled operational disruption or regulatory penalty. Immediate remediation required before next contract renewal.`
    : rr === 'Medium'
    ? `Residual risk is 🟠 <strong>MEDIUM</strong> — ${missing.length > 0 ? missing.length + ' gap(s) reduce contractual control effectiveness. Address before next renewal to avoid DORA supervisory exposure.' : 'Non-critical classification limits inherent impact, but identified gaps should be remediated at next review.'}`
    : `Residual risk is 🟢 <strong>LOW</strong> — contractual controls are substantially in place. Maintain monitoring and re-assess at next contract review cycle.`;

  const content = document.getElementById('contract-modal-content');
  content.innerHTML = `
    <h3 style="margin:0 0 4px">${a.supplier_name}</h3>
    <p style="color:var(--muted);font-size:12px;margin:0 0 12px">${a.contract_reference||''}${legLine ? ' &nbsp;·&nbsp; <span style="color:var(--accent)">' + legLine + '</span>' : ''}</p>
    <p style="font-size:13px;line-height:1.7;margin:0 0 14px;padding:12px 14px;background:var(--surface2);border-radius:6px;border-left:3px solid var(--accent)">${classNarrative}</p>
    <div class="summary-cards" style="margin-bottom:14px">
      <div class="card"><div class="label">Classification</div><div style="font-size:13px;margin-top:4px">${classBadge(a.classification)}</div></div>
      <div class="card"><div class="label">Satisfied</div><div class="value green">${satisfied.length}</div></div>
      <div class="card"><div class="label">Missing / Ambiguous</div><div class="value ${missing.length > 0 ? 'red' : 'green'}">${missing.length}</div></div>
      <div class="card"><div class="label">Control Strength</div><div class="value ${csClass}" style="font-size:13px">${cs}</div></div>
    </div>
    <p style="font-size:13px;line-height:1.7;margin:0 0 14px;padding:12px 14px;background:var(--bg);border-radius:6px;border-left:3px solid ${rr==='High'?'var(--red)':rr==='Medium'?'var(--orange)':'var(--green)'}">${riskNarrative}</p>
    ${missing.length ? `<div class="section-title" style="margin-bottom:8px">Missing / Ambiguous Requirements (${missing.length})</div>
    <ol style="margin:0;padding-left:20px;font-size:12px;color:var(--text);line-height:1.8">
      ${missing.map(m => '<li><strong style="color:var(--accent)">' + (m.requirement_article||m.requirement_id) + '</strong> — ' + (m.requirement_summary||'') + '</li>').join('')}
    </ol>` : '<p style="color:var(--green);font-size:13px">&#10003; No missing requirements — all assessed obligations are satisfied.</p>'}
    <div style="margin-top:20px;text-align:right">
      <button onclick="document.getElementById('contract-modal').classList.remove('open');showTab('contracts');showContractDetail('${contractKey}')" style="background:var(--accent);color:#fff;border:none;padding:8px 18px;border-radius:6px;cursor:pointer;font-size:13px">View Full Gap Analysis &#8594;</button>
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
  container.innerHTML = '<div class="loading">Loading board report&#8230;</div>';
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

  // --- Section 1: Open / Closed Findings ---
  const sec1 = (() => {
    const findingsHtml = openFindings.length ? `
    <div class="table-wrap"><table>
      <thead><tr><th>Finding</th><th>Severity</th><th>Owner</th><th>Due</th><th>Condition (5-C)</th><th>Recommendation</th></tr></thead>
      <tbody>${openFindings.map(f => `<tr>
        <td style="font-size:12px;font-weight:600">${f.finding_id}: ${f.title}</td>
        <td><span class="badge" style="background:var(--surface2);color:${sevColor(f.severity)}">${f.severity}</span></td>
        <td style="font-size:12px;color:var(--muted)">${f.owner||'—'}</td>
        <td style="font-size:12px;color:${f.target_remediation_date?'var(--yellow)':'var(--muted)'}">${f.target_remediation_date||'TBD'}</td>
        <td style="font-size:12px;max-width:180px">${f.condition||'—'}</td>
        <td style="font-size:12px;color:var(--accent);max-width:180px">${f.recommendation||'—'}</td>
      </tr>`).join('')}</tbody>
    </table></div>` : '<p style="color:var(--green);font-size:13px">&#10003; No open findings.</p>';
    const closedHtml = closedFindings.length ? `<div style="margin-top:12px;font-size:12px;color:var(--muted)">${closedFindings.length} finding(s) closed this period — no further action required.</div>` : '';
    return `<div class="board-section" style="margin-bottom:24px">
      <div class="section-title" style="color:${openFindings.length?'var(--orange)':'var(--green)'};margin-bottom:8px">${openFindings.length?'&#9888;':'&#10003;'} Open / Closed Findings — ${openFindings.length} open, ${closedFindings.length} closed</div>
      ${findingsHtml}${closedHtml}
    </div>`;
  })();

  // --- Section 2: Finalised Reports ---
  const sec2 = `<div class="board-section" style="margin-bottom:24px">
    <div class="section-title" style="margin-bottom:4px">Finalised Audit Reports — Board Reporting Register</div>
    <p style="font-size:12px;color:var(--muted);margin:0 0 12px">Once an audit report is finalised and presented to the board, it is tracked here for follow-up only. Internal Audit monitors implementation of agreed actions.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Report</th><th>Presented</th><th>Findings</th><th>Open</th><th>Closed</th><th>Status</th></tr></thead>
      <tbody>
        <tr>
          <td style="font-size:12px"><strong>DORA Art. 30 ICT Contract Gap Analysis — FY 2024</strong><br><code style="font-size:10px">IAR-2024-003</code></td>
          <td style="font-size:12px;color:var(--muted)">2024-12-10<br>Board meeting #Q4-2024</td>
          <td style="font-size:12px">4</td>
          <td style="font-size:12px;color:var(--orange)">2</td>
          <td style="font-size:12px;color:var(--green)">2</td>
          <td><span style="background:rgba(251,191,36,0.2);color:var(--yellow);border-radius:4px;padding:2px 8px;font-size:11px">In Progress</span></td>
        </tr>
        <tr>
          <td style="font-size:12px"><strong>ISO 27001 Information Security Review — H1 2024</strong><br><code style="font-size:10px">IAR-2024-001</code></td>
          <td style="font-size:12px;color:var(--muted)">2024-06-25<br>Board meeting #Q2-2024</td>
          <td style="font-size:12px">3</td>
          <td style="font-size:12px;color:var(--green)">0</td>
          <td style="font-size:12px;color:var(--green)">3</td>
          <td><span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 8px;font-size:11px">All Closed</span></td>
        </tr>
      </tbody>
    </table></div>
  </div>`;

  // --- Section 3: Prior Period Follow-Up ---
  const sec3 = `<div class="board-section" style="margin-bottom:24px">
    <div class="section-title" style="color:var(--orange);margin-bottom:4px">&#9654; Prior Period Follow-Up Action Tracker</div>
    <p style="font-size:12px;color:var(--muted);margin:0 0 12px">Findings from finalised, board-reported audits. Internal Audit tracks implementation. Resolution is confirmed before a finding is marked Closed.</p>
    <div class="info-box" style="margin-bottom:10px;font-size:12px"><strong>Extension approval rules:</strong> <strong style="color:var(--red)">P1 (Critical)</strong> — extension requires L2 (CRO/CFO) sign-off; must resolve within 3 months of closing meeting or risk is formally accepted at L2 level. <strong style="color:var(--orange)">P2 (High)</strong> — one extension permitted, requires L3 (Head of IA) approval; must resolve within 12 months of closing meeting. <strong style="color:var(--yellow)">P3 (Medium)</strong> — one extension permitted, approved by the engagement auditor; must resolve within 12 months of closing meeting. All findings must be closed within 12 months of the closing meeting.</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Finding</th><th>Source</th><th>Priority</th><th>Owner</th><th>Original Due</th><th>Revised Due</th><th>Days Overdue</th><th>Extension #</th><th>Extension Approved By</th><th>Status</th></tr></thead>
      <tbody>
        <tr>
          <td style="font-size:12px"><strong>F-2024-003-01</strong><br>Exit strategy not documented (NNS-NET-2021-007)</td>
          <td style="font-size:11px;color:var(--muted)">IAR-2024-003</td>
          <td><span style="color:var(--red);font-size:12px;font-weight:600">HIGH</span></td>
          <td style="font-size:12px">Legal &amp; Procurement</td>
          <td style="font-size:12px;color:var(--muted)">2025-03-31</td>
          <td style="font-size:12px;color:var(--orange)">2025-06-30 &#8593;</td>
          <td style="font-size:12px;color:var(--red);font-weight:600">+91</td>
          <td style="font-size:12px;text-align:center">1</td>
          <td style="font-size:12px;color:var(--muted)">Head of IA</td>
          <td><span style="background:rgba(251,191,36,0.2);color:var(--yellow);border-radius:4px;padding:2px 8px;font-size:11px">Overdue</span></td>
        </tr>
        <tr>
          <td style="font-size:12px"><strong>F-2024-003-02</strong><br>PayLink contract — DORA Art. 30(2)(d) SLAs missing</td>
          <td style="font-size:11px;color:var(--muted)">IAR-2024-003</td>
          <td><span style="color:var(--orange);font-size:12px;font-weight:600">MEDIUM</span></td>
          <td style="font-size:12px">Contract Manager</td>
          <td style="font-size:12px;color:var(--muted)">2025-05-31</td>
          <td style="font-size:12px;color:var(--green)">2025-05-31</td>
          <td style="font-size:12px;color:var(--muted)">0</td>
          <td style="font-size:12px;text-align:center">0</td>
          <td style="font-size:12px;color:var(--muted)">—</td>
          <td><span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 8px;font-size:11px">On Track</span></td>
        </tr>
      </tbody>
    </table></div>
  </div>`;

  // --- Section 4: LFAP with period tabs ---
  const sec4 = `<div class="board-section" style="margin-bottom:24px">
    <div class="section-title" style="margin-bottom:8px">Log of Follow-up Actions (LFAP) — by Period</div>
    <p style="font-size:12px;color:var(--muted);margin:0 0 10px">The LFAP aggregates all prior-period management actions by quarter and half-year. Used for board-level trend reporting.</p>
    <div class="lfap-tabs">
      <div class="lfap-tab active" onclick="showLFAP('Q1',this)">Q1</div>
      <div class="lfap-tab" onclick="showLFAP('Q2',this)">Q2</div>
      <div class="lfap-tab" onclick="showLFAP('Q3',this)">Q3</div>
      <div class="lfap-tab" onclick="showLFAP('Q4',this)">Q4</div>
      <div class="lfap-tab" onclick="showLFAP('1H',this)">1H</div>
      <div class="lfap-tab" onclick="showLFAP('Full Year',this)">Full Year</div>
    </div>
    <div id="lfap-content"></div>
  </div>`;

  // --- Section 5: Board Intelligence Value Tracker ---
  const sec5 = `<div class="board-section" style="margin-bottom:24px">
    <div class="section-title" style="margin-bottom:8px">Board Intelligence — Value Tracker</div>
    <p style="font-size:12px;color:var(--muted);margin:0 0 12px">Control improvements, risk reductions, and compliance milestones surfaced this period by Internal Audit.</p>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px">
      <div style="background:rgba(74,222,128,0.07);border:1px solid rgba(74,222,128,0.2);border-radius:8px;padding:14px">
        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--green);font-weight:600;margin-bottom:6px">Control Improvements</div>
        <div style="font-size:13px;line-height:1.6">NNS-NET-2021-003 contract schedule fully remediated. DORA Art. 30(2)(a)–(c) now met across all critical ICT providers.</div>
      </div>
      <div style="background:rgba(124,106,255,0.07);border:1px solid rgba(124,106,255,0.2);border-radius:8px;padding:14px">
        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--accent);font-weight:600;margin-bottom:6px">Risk Reductions</div>
        <div style="font-size:13px;line-height:1.6">Residual risk on AU01 (Identity &amp; Access) reduced from MEDIUM to LOW following access review remediation and quarterly re-certification rollout.</div>
      </div>
      <div style="background:rgba(251,191,36,0.07);border:1px solid rgba(251,191,36,0.2);border-radius:8px;padding:14px">
        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--yellow);font-weight:600;margin-bottom:6px">Compliance Milestones</div>
        <div style="font-size:13px;line-height:1.6">DORA Art. 28(3) register completeness reached 100% for Tier 1 ICT providers. Art. 30 contract schedule annex signed by all critical providers.</div>
      </div>
    </div>
  </div>`;

  // --- Section 6: Next Quarter Focus ---
  const sec6 = `<div class="board-section" style="margin-bottom:24px">
    <div class="section-title" style="margin-bottom:4px">Next Quarter Focus — Internal Audit Plan</div>
    <div style="background:rgba(251,146,60,0.1);border:1px solid rgba(251,146,60,0.3);border-radius:6px;padding:10px 14px;margin-bottom:12px;font-size:12px;color:var(--orange)">&#9888; <strong>Human review required</strong> before distributing to governance bodies.</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px">
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);margin-bottom:6px">Q1 Priority</div>
        <div style="font-size:12px;line-height:1.6">Follow-up on F-2024-003-01 (Exit Strategy). Target closure 2025-06-30. Escalation path to CRO if overdue again.</div>
      </div>
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);margin-bottom:6px">Planned Engagements</div>
        <div style="font-size:12px;line-height:1.6">ISO 27001 access control deep-dive (AU02). GDPR Art. 28 processor agreements refresh. DORA concentration risk assessment (new).</div>
      </div>
      <div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px">
        <div style="font-size:12px;font-weight:600;color:var(--accent);margin-bottom:6px">Supervisory Readiness</div>
        <div style="font-size:12px;line-height:1.6">Prepare DORA register package for potential supervisory review in Q2. Update ICT risk assessment with Q4 findings.</div>
      </div>
    </div>
  </div>`;

  // Draft Audit Reports (shown at top of board report)
  const darSection = `<div class="board-section" style="margin-bottom:24px">
    <div class="section-title" style="margin-bottom:4px">Draft Audit Reports — Pending Board Submission</div>
    <p style="font-size:12px;color:var(--muted);margin:0 0 14px">Reports in draft or in-review status. Once finalised they move to the Finalised Reports register below. Click a card to view scope and findings summary.</p>
    ${[
      {id:'DAR-2025-001',title:'ISO 27001 Information Security Controls Assessment — Q4 2025',scope:'AU01–AU05',status:'Draft',date:'2025-12-15',findings:2,auditor:'Internal Audit'},
      {id:'DAR-2025-002',title:'DORA Art. 30 ICT Third-Party Contract Gap Analysis — FY 2025',scope:'5 ICT contracts',status:'Draft',date:'2025-12-20',findings:1,auditor:'IT Audit'},
      {id:'DAR-2025-003',title:'BCP / DR Operational Resilience Review — Q4 2025',scope:'AU07 (BCP)',status:'In Review',date:'2025-11-30',findings:0,auditor:'Internal Audit'},
    ].map(r => {
      const badge = r.status==='Draft'?'<span style="background:rgba(124,106,255,0.2);color:var(--accent);border-radius:4px;padding:2px 8px;font-size:11px">Draft</span>'
        :'<span style="background:rgba(251,191,36,0.2);color:var(--yellow);border-radius:4px;padding:2px 8px;font-size:11px">In Review</span>';
      return '<div style="background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:10px">'
        +'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">'
        +'<div style="font-size:14px;font-weight:600">'+r.title+'</div>'+badge+'</div>'
        +'<div style="font-size:12px;color:var(--muted)">'
        +'<span style="margin-right:16px"><code>'+r.id+'</code></span>'
        +'<span style="margin-right:16px">Scope: '+r.scope+'</span>'
        +'<span style="margin-right:16px">Target: '+r.date+'</span>'
        +'<span style="margin-right:16px">By: '+r.auditor+'</span>'
        +(r.findings>0?'<span style="color:var(--orange)">'+r.findings+' open finding(s)</span>':'<span style="color:var(--green)">No findings</span>')
        +'</div></div>';
    }).join('')}
  </div>`;

  container.innerHTML = darSection + `
    <!-- Executive summary cards -->
    <div class="summary-cards" style="margin-bottom:24px">
      <div class="card"><div class="label">Total Controls Tested</div><div class="value">${wpData.tested_count||0} / ${wpData.total_controls||0}</div></div>
      <div class="card"><div class="label">With Findings</div><div class="value ${(wpData.exception_count||0)>0?'yellow':''}">${wpData.exception_count||0}</div></div>
      <div class="card"><div class="label">Open Findings</div><div class="value ${openFindings.length>0?'red':''}">${openFindings.length}</div></div>
      <div class="card"><div class="label">Closed Findings</div><div class="value green">${closedFindings.length}</div></div>
      <div class="card"><div class="label">Register Gaps</div><div class="value ${(recon.register_only||0)>0?'red':''}">${recon.register_only||0}</div></div>
    </div>
    <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:20px;font-size:11px;color:var(--muted);border:1px solid var(--border);border-radius:6px;padding:8px 14px;background:var(--surface)">
      <span style="font-weight:600;color:var(--text);margin-right:4px">Definitions:</span>
      <span><strong style="color:var(--text)">Control Strength:</strong> Effective (0 findings) &nbsp;·&nbsp; Partially Effective (1–5) &nbsp;·&nbsp; Weak Controls (6+)</span>
      <span style="border-left:1px solid var(--border);padding-left:12px"><strong style="color:var(--text)">Residual Risk:</strong> Inherent risk score adjusted by control strength</span>
      <span style="border-left:1px solid var(--border);padding-left:12px"><strong style="color:var(--text)">Risk Matrix:</strong> <span style="color:var(--red)">■</span> High (6–9) &nbsp; <span style="color:var(--orange)">■</span> Medium (3–5) &nbsp; <span style="color:var(--green)">■</span> Low (1–2)</span>
    </div>

    <!-- Risk Matrix for Board -->
    <div style="margin-bottom:24px">
      <div class="section-title" style="margin-bottom:4px">Risk Matrix — Impact &#215; Likelihood (3&#215;3)</div>
      <p style="font-size:12px;color:var(--muted);margin:0 0 12px">Board-ready risk quadrant. Red = High risk (score 6–9) &nbsp;|&nbsp; Orange = Medium risk (3–5) &nbsp;|&nbsp; Green = Low risk (1–2).</p>
      <div style="display:grid;grid-template-columns:60px repeat(3,1fr);gap:4px;max-width:720px">
        <div style="font-size:10px;color:var(--muted);display:flex;align-items:flex-end;padding-bottom:4px;line-height:1.3">Impact &#8595;<br>Likelihood &#8594;</div>
        <div style="font-size:11px;color:var(--muted);text-align:center;padding:4px 2px;border-bottom:1px solid var(--border)">Low (1)</div>
        <div style="font-size:11px;color:var(--muted);text-align:center;padding:4px 2px;border-bottom:1px solid var(--border)">Medium (2)</div>
        <div style="font-size:11px;color:var(--muted);text-align:center;padding:4px 2px;border-bottom:1px solid var(--border)">High (3)</div>
        <!-- Row: Impact High (3) -->
        <div style="font-size:10px;color:var(--muted);display:flex;align-items:center;justify-content:flex-end;padding-right:6px;text-align:right">High (3)</div>
        <div style="background:rgba(251,146,60,0.18);border:1px solid #fb923c;border-radius:6px;padding:6px;min-height:70px;font-size:10px;line-height:1.5">
          <div>&#8226; BCP / DR Not Fully Tested</div>
        </div>
        <div style="background:rgba(248,113,113,0.18);border:1px solid #f87171;border-radius:6px;padding:6px;min-height:70px;font-size:10px;line-height:1.5">
          <div>&#8226; Access Control Gaps (IAM)</div>
          <div>&#8226; Exit Strategy Missing</div>
        </div>
        <div style="background:rgba(248,113,113,0.18);border:1px solid #f87171;border-radius:6px;padding:6px;min-height:70px;font-size:10px;line-height:1.5">
          <div>&#8226; ICT Contract Gaps (DORA Art.30)</div>
        </div>
        <!-- Row: Impact Medium (2) -->
        <div style="font-size:10px;color:var(--muted);display:flex;align-items:center;justify-content:flex-end;padding-right:6px;text-align:right">Med (2)</div>
        <div style="background:rgba(74,222,128,0.14);border:1px solid #4ade80;border-radius:6px;padding:6px;min-height:70px;font-size:10px;line-height:1.5">
          <div>&#8226; Encryption Key Rotation</div>
        </div>
        <div style="background:rgba(251,146,60,0.18);border:1px solid #fb923c;border-radius:6px;padding:6px;min-height:70px;font-size:10px;line-height:1.5">
          <div>&#8226; DPA Art.28 Gaps (GDPR)</div>
          <div>&#8226; SLA / Sub-contractor Clause Missing</div>
        </div>
        <div style="background:rgba(248,113,113,0.18);border:1px solid #f87171;border-radius:6px;padding:6px;min-height:70px;font-size:10px;line-height:1.5">
          <div>&#8226; Patch / Vuln Management Delays</div>
        </div>
        <!-- Row: Impact Low (1) -->
        <div style="font-size:10px;color:var(--muted);display:flex;align-items:center;justify-content:flex-end;padding-right:6px;text-align:right">Low (1)</div>
        <div style="background:rgba(74,222,128,0.14);border:1px solid #4ade80;border-radius:6px;padding:6px;min-height:70px;font-size:10px;line-height:1.5">
          <div>&#8226; DSR Response Logging</div>
        </div>
        <div style="background:rgba(74,222,128,0.14);border:1px solid #4ade80;border-radius:6px;padding:6px;min-height:70px;font-size:10px;line-height:1.5">
          <div>&#8226; User Training Compliance</div>
          <div>&#8226; Register Maintenance Lag</div>
        </div>
        <div style="background:rgba(251,146,60,0.18);border:1px solid #fb923c;border-radius:6px;padding:6px;min-height:70px;font-size:10px;line-height:1.5">
          <div style="color:var(--muted)">—</div>
        </div>
      </div>
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

    <!-- Quarterly audit conclusions -->
    <div class="board-section" style="margin-top:24px">
      <div class="section-title" style="margin-bottom:4px">Quarterly Audit Conclusions — Reports Completed This Period</div>
      <p style="font-size:12px;color:var(--muted);margin:0 0 12px">Summary conclusions from internal audit reports completed and presented in the current quarter. Each conclusion is supported by working papers available for supervisory review.</p>
      ${wps.length === 0 ? '<p style="color:var(--muted);font-size:12px">No reports completed this period.</p>' : `
      <div style="display:flex;flex-direction:column;gap:10px">
        ${[...new Set(wps.map(w => w.control_domain).filter(Boolean))].map(domain => {
          const domWps = wps.filter(w => w.control_domain === domain);
          const exc = domWps.filter(w => w.test_result === 'EXCEPTION').length;
          const passed = domWps.filter(w => w.test_result === 'PASSED').length;
          const conclusion = exc > 0
            ? `Testing identified ${exc} finding(s) requiring management action. ${passed} control(s) are operating effectively.`
            : `All ${passed} control(s) tested are operating effectively. No findings raised.`;
          const borderColor = exc > 0 ? 'var(--orange)' : 'var(--green)';
          const icon = exc > 0 ? '&#9888;' : '&#10003;';
          return `<div style="border-left:3px solid ${borderColor};padding:10px 14px;background:var(--surface);border-radius:0 6px 6px 0">
            <div style="font-size:13px;font-weight:600;margin-bottom:3px">${icon} ${domain}</div>
            <div style="font-size:12px;color:var(--muted);line-height:1.6">${conclusion}</div>
          </div>`;
        }).join('')}
      </div>`}
    </div>

    <!-- Findings overview by priority -->
    <div class="board-section" style="margin-top:24px">
      <div class="section-title" style="margin-bottom:4px">Findings Overview — Quarter at a Glance</div>
      <p style="font-size:12px;color:var(--muted);margin:0 0 12px">New, open and closed findings broken down by priority. Priority 1 = Critical/High; Priority 2 = Medium; Priority 3 = Low.</p>
      <div style="display:grid;grid-template-columns:120px 1fr 1fr 1fr;gap:0;border:1px solid var(--border);border-radius:6px;overflow:hidden;font-size:12px">
        <div style="background:var(--surface2);font-weight:600;padding:8px 12px;border-bottom:1px solid var(--border)">Priority</div>
        <div style="background:var(--surface2);font-weight:600;padding:8px 12px;border-bottom:1px solid var(--border);text-align:center">New</div>
        <div style="background:var(--surface2);font-weight:600;padding:8px 12px;border-bottom:1px solid var(--border);text-align:center">Open</div>
        <div style="background:var(--surface2);font-weight:600;padding:8px 12px;border-bottom:1px solid var(--border);text-align:center">Closed</div>
        ${['CRITICAL','HIGH','MEDIUM','LOW'].reduce((acc, sev, i) => {
          const priorityLabel = (sev === 'CRITICAL' || sev === 'HIGH') ? 'Priority 1' : sev === 'MEDIUM' ? 'Priority 2' : 'Priority 3';
          if ((sev === 'HIGH' && acc.includes('Priority 1'))) return acc;
          const allFindings = wps.flatMap(w => w.findings || []);
          const sevFindings = allFindings.filter(f => {
            const s = (f.severity||'').toUpperCase();
            if (priorityLabel === 'Priority 1') return s === 'CRITICAL' || s === 'HIGH';
            if (priorityLabel === 'Priority 2') return s === 'MEDIUM';
            return s === 'LOW';
          });
          const newCount = sevFindings.length;
          const openCount = sevFindings.filter(f => f.status === 'open').length;
          const closedCount = sevFindings.filter(f => f.status === 'closed').length;
          const color = priorityLabel === 'Priority 1' ? 'var(--red)' : priorityLabel === 'Priority 2' ? 'var(--orange)' : 'var(--yellow)';
          const bg = i < 3 ? 'border-bottom:1px solid var(--border)' : '';
          return acc + `<div style="padding:8px 12px;${bg};font-weight:600;color:${color}">${priorityLabel}</div>
            <div style="padding:8px 12px;${bg};text-align:center">${newCount}</div>
            <div style="padding:8px 12px;${bg};text-align:center;color:${openCount > 0 ? 'var(--orange)' : 'var(--muted)'}">${openCount}</div>
            <div style="padding:8px 12px;${bg};text-align:center;color:${closedCount > 0 ? 'var(--green)' : 'var(--muted)'}">${closedCount}</div>`;
        }, '')}
        ${(() => {
          const allF = wps.flatMap(w => w.findings || []);
          const tNew = allF.length, tOpen = allF.filter(f=>f.status==='open').length, tClosed = allF.filter(f=>f.status==='closed').length;
          return `<div style="padding:8px 12px;border-top:2px solid var(--border);font-weight:700">Total</div>
            <div style="padding:8px 12px;border-top:2px solid var(--border);text-align:center;font-weight:700">${tNew}</div>
            <div style="padding:8px 12px;border-top:2px solid var(--border);text-align:center;font-weight:700;color:${tOpen>0?'var(--orange)':'var(--muted)'}">${tOpen}</div>
            <div style="padding:8px 12px;border-top:2px solid var(--border);text-align:center;font-weight:700;color:${tClosed>0?'var(--green)':'var(--muted)'}">${tClosed}</div>`;
        })()}
      </div>
    </div>

    <!-- Finalized audit reports (once reported to board, tracked for follow-up only) -->
    <div class="board-section" style="margin-top:24px">
      <div class="section-title" style="margin-bottom:4px">Finalised Audit Reports — Board Reporting Register</div>
      <p style="font-size:12px;color:var(--muted);margin:0 0 12px">Once an audit report is finalised and presented to the board, it is tracked here for follow-up resolution only. The Internal Audit function monitors implementation of agreed actions. New timeline / owner / priority changes are recorded below.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Report</th><th>Presented</th><th>Findings</th><th>Open</th><th>Closed</th><th>Status</th></tr></thead>
        <tbody>
          <tr>
            <td style="font-size:12px"><strong>DORA Art. 30 ICT Contract Gap Analysis — FY 2024</strong><br><code style="font-size:10px">IAR-2024-003</code></td>
            <td style="font-size:12px;color:var(--muted)">2024-12-10<br>Board meeting #Q4-2024</td>
            <td style="font-size:12px">4</td>
            <td style="font-size:12px;color:var(--orange)">2</td>
            <td style="font-size:12px;color:var(--green)">2</td>
            <td><span style="background:rgba(251,191,36,0.2);color:var(--yellow);border-radius:4px;padding:2px 8px;font-size:11px">In Progress</span></td>
          </tr>
          <tr>
            <td style="font-size:12px"><strong>ISO 27001 Information Security Review — H1 2024</strong><br><code style="font-size:10px">IAR-2024-001</code></td>
            <td style="font-size:12px;color:var(--muted)">2024-06-25<br>Board meeting #Q2-2024</td>
            <td style="font-size:12px">3</td>
            <td style="font-size:12px;color:var(--green)">0</td>
            <td style="font-size:12px;color:var(--green)">3</td>
            <td><span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 8px;font-size:11px">All Closed</span></td>
          </tr>
        </tbody>
      </table></div>
    </div>

    <!-- Follow-up action tracking (prior period findings only) -->
    <div class="board-section" style="margin-top:24px">
      <div class="section-title" style="color:var(--orange);margin-bottom:4px">&#9654; Follow-Up Action Tracker — Prior Period Findings</div>
      <p style="font-size:12px;color:var(--muted);margin:0 0 12px">These are findings from finalised, board-reported audits. Internal Audit tracks implementation. Priority and owner may be upgraded or downgraded; deadlines extended with justification. Resolution is confirmed by Internal Audit before a finding is marked Closed.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Finding</th><th>Source Report</th><th>Original Priority</th><th>Current Priority</th><th>Owner</th><th>Original Deadline</th><th>Revised Deadline</th><th>Status</th></tr></thead>
        <tbody>
          <tr>
            <td style="font-size:12px"><strong>F-2024-003-01</strong><br>Exit strategy not documented (NNS-NET-2021-007)</td>
            <td style="font-size:11px;color:var(--muted)">IAR-2024-003</td>
            <td><span style="color:var(--red);font-size:12px;font-weight:600">HIGH</span></td>
            <td><span style="color:var(--red);font-size:12px;font-weight:600">HIGH</span> <span style="font-size:10px;color:var(--muted)">(unchanged)</span></td>
            <td style="font-size:12px">Legal &amp; Procurement</td>
            <td style="font-size:12px;color:var(--muted)">2025-03-31</td>
            <td style="font-size:12px;color:var(--orange)">2025-06-30 &#8593;</td>
            <td><span style="background:rgba(251,191,36,0.2);color:var(--yellow);border-radius:4px;padding:2px 8px;font-size:11px">Overdue</span></td>
          </tr>
          <tr>
            <td style="font-size:12px"><strong>F-2024-003-02</strong><br>PayLink contract — DORA Art. 30(2)(d) SLAs missing</td>
            <td style="font-size:11px;color:var(--muted)">IAR-2024-003</td>
            <td><span style="color:var(--orange);font-size:12px;font-weight:600">MEDIUM</span></td>
            <td><span style="color:var(--orange);font-size:12px;font-weight:600">MEDIUM</span> <span style="font-size:10px;color:var(--muted)">(unchanged)</span></td>
            <td style="font-size:12px">Contract Manager</td>
            <td style="font-size:12px;color:var(--muted)">2025-05-31</td>
            <td style="font-size:12px;color:var(--green)">2025-05-31</td>
            <td><span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 8px;font-size:11px">On Track</span></td>
          </tr>
        </tbody>
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
  showLFAP('Q1', document.querySelector('.lfap-tab'));
}

function _dbgStatus(msg) {
  const el = document.getElementById('load-status');
  if (el) { el.style.display='block'; el.textContent = '⏳ ' + msg; }
  console.log('[dashboard]', msg);
}
function _dbgClear() {
  const el = document.getElementById('load-status');
  if (el) el.style.display = 'none';
}

async function loadOverview() {
  let analysisRes, registerRes, reconRes;
  _dbgStatus('Fetching /api/analyses, /api/register, /api/reconciliation…');
  try {
    [analysisRes, registerRes, reconRes] = await Promise.all([
      fetch('/api/analyses'),
      fetch('/api/register'),
      fetch('/api/reconciliation'),
    ]);
  } catch(e) {
    document.getElementById('overview-table').innerHTML =
      `<tr><td colspan="7" class="empty" style="color:var(--red)">Could not reach API: ${e.message}. Is the server running?</td></tr>`;
    return;
  }
  if (!analysisRes.ok) {
    document.getElementById('overview-table').innerHTML =
      `<tr><td colspan="7" class="empty" style="color:var(--red)">API error ${analysisRes.status} on /api/analyses</td></tr>`;
    return;
  }
  _dbgStatus('Parsing JSON responses…');
  analyses = await analysisRes.json();
  registerEntries = await registerRes.json();
  const recon = await reconRes.json();

  // P1/P2/P3 KPI cards — derived from working papers open findings severity
  // Fall back to contract gap severity if no working papers loaded
  let p1 = 0, p2 = 0, p3 = 0;
  try {
    const wpCtrl = new AbortController();
    const wpTimer = setTimeout(() => wpCtrl.abort(), 3000);
    const wpRes = await fetch('/api/workpapers', { signal: wpCtrl.signal });
    clearTimeout(wpTimer);
    const wpData = await wpRes.json();
    if (!wpData.message && wpData.work_papers) {
      wpData.work_papers.forEach(wp => {
        (wp.findings || []).forEach(f => {
          if (f.status === 'open' || !f.status) {
            const sev = (f.severity || '').toUpperCase();
            if (sev === 'CRITICAL') p1++;
            else if (sev === 'HIGH') p1++;
            else if (sev === 'MEDIUM') p2++;
            else if (sev === 'LOW') p3++;
          }
        });
      });
    }
  } catch(e) { /* working papers not loaded or timed out — leave at 0 */ }

  document.getElementById('stat-p1').textContent = p1 || '0';
  document.getElementById('stat-p2').textContent = p2 || '0';
  document.getElementById('stat-p3').textContent = p3 || '0';

  // Critical contract gaps: Critical/Important contracts with 6+ missing requirements
  const critGaps = analyses.filter(a =>
    a.classification === 'critical_important' && a.summary && (a.summary.missing || 0) >= 6
  ).length;
  document.getElementById('stat-critical-gaps').textContent = critGaps;

  // Next audit activity — show first open finding or next review
  const nextAuditEl = document.getElementById('stat-next-audit');
  if (critGaps > 0) {
    nextAuditEl.innerHTML = `<span style="color:var(--red)">Review ${critGaps} critical gap${critGaps > 1 ? 's' : ''}</span>`;
  } else if (p1 > 0) {
    nextAuditEl.innerHTML = `<span style="color:var(--orange)">Remediate ${p1} P1 finding${p1 > 1 ? 's' : ''}</span>`;
  } else {
    nextAuditEl.innerHTML = '<span style="color:var(--green)">No critical actions</span>';
  }

  // Overview table — 5 columns: Supplier, Criticality, Open Findings, Contract Gaps, Status
  _dbgStatus(`Rendering ${analyses.length} suppliers…`);
  const tbody = document.getElementById('overview-table');
  if (!analyses.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">No analyses loaded</td></tr>';
  } else {
    const rrOrder = {High:0,Medium:1,Low:2};
    const sorted = [...analyses].sort((a, b) => {
      const missA = a.summary ? (a.summary.missing || 0) : 0;
      const missB = b.summary ? (b.summary.missing || 0) : 0;
      const rrA = residualRisk(a.classification, missA);
      const rrB = residualRisk(b.classification, missB);
      return (rrOrder[rrA] ?? 9) - (rrOrder[rrB] ?? 9);
    });
    tbody.innerHTML = sorted.map((a) => {
      const miss = a.summary ? (a.summary.missing || 0) : 0;
      const rr = residualRisk(a.classification, miss);
      const rrC = residualRiskClass(a.classification, miss);
      // Count findings for this supplier
      const suppFinds = {p1:0,p2:0,p3:0};
      // (findings come from working papers — show gap count as proxy if no WP data)
      const findingsCell = suppFinds.p1 + suppFinds.p2 + suppFinds.p3 > 0
        ? `<span style="color:var(--red)">${suppFinds.p1} P1</span> <span style="color:var(--orange)">${suppFinds.p2} P2</span> <span style="color:var(--yellow)">${suppFinds.p3} P3</span>`
        : '<span style="color:var(--muted);font-size:11px">See Audit Papers</span>';
      const gapBadge = miss === 0
        ? '<span style="color:var(--green);font-size:12px">&#10003; No gaps</span>'
        : `<span class="badge ${rrC}" style="font-size:12px">${miss} gap${miss > 1 ? 's' : ''}</span>`;
      return `<tr onclick="showContractDetail('${(a.contract_key || a.analysis_id).replace(/'/g,"\\'")}')" style="cursor:pointer">
        <td><strong>${a.supplier_name}</strong></td>
        <td>${classBadge(a.classification)}</td>
        <td>${findingsCell}</td>
        <td>${gapBadge}</td>
        <td>${statusBadge(a.overall_status)}</td>
      </tr>`;
    }).join('');
  }
  _dbgClear();
}

async function loadContracts() {
  const tbody = document.getElementById('contracts-table');
  if (!analyses.length) {
    tbody.innerHTML = '<tr><td colspan="5" class="empty">No analyses loaded</td></tr>';
    return;
  }
  const rrOrder = {High:0,Medium:1,Low:2};
  const sorted = [...analyses].sort((a, b) => {
    const missA = a.summary ? (a.summary.missing || 0) : 0;
    const missB = b.summary ? (b.summary.missing || 0) : 0;
    const rrA = residualRisk(a.classification, missA);
    const rrB = residualRisk(b.classification, missB);
    return (rrOrder[rrA] ?? 9) - (rrOrder[rrB] ?? 9);
  });
  tbody.innerHTML = sorted.map((a) => {
    const miss = a.summary ? (a.summary.missing || 0) : 0;
    const rr = residualRisk(a.classification, miss);
    const rrC = residualRiskClass(a.classification, miss);
    const gaps = (a.top_gaps || []);
    const topGapReqId = gaps.length ? gaps[0].req_id : null;
    const consequenceText = topGapReqId ? getConsequence(topGapReqId) : (miss > 0 ? 'Missing contractual clauses expose the entity to regulatory and operational risk.' : '');
    const consequenceCell = miss > 0
      ? `<span style="font-size:12px;color:var(--muted);line-height:1.4">${consequenceText}</span>`
      : `<span style="font-size:12px;color:var(--green)">&#10003; No gaps</span>`;
    const gapCell = miss === 0
      ? '<span style="color:var(--green);font-size:12px">&#10003; None</span>'
      : `<span class="badge ${rrC}">${miss} gap${miss > 1 ? 's' : ''}</span>`;
    return `<tr onclick="showContractDetail('${(a.contract_key || a.analysis_id).replace(/'/g,"\\'")}')" style="cursor:pointer">
      <td><strong>${a.supplier_name}</strong></td>
      <td>${classBadge(a.classification)}</td>
      <td>${gapCell}</td>
      <td style="max-width:220px">${consequenceCell}</td>
      <td>${statusBadge(a.overall_status)}</td>
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

  const statusLabel = s => ({missing:'Non-Compliant',ambiguous:'Needs Clarification',satisfied:'Compliant',na:'N/A',not_assessed:'Not Assessed'}[s]||s);
  const statusCls   = s => ({missing:'status-missing',ambiguous:'status-ambiguous',satisfied:'status-satisfied',na:'status-na'}[s]||'');
  const riskLabel   = r => r ? r.replace(/_/g,' ') : '—';
  const effortBadge = (lvl, days) => {
    if (!lvl) return '<span style="color:var(--muted)">—</span>';
    const c = lvl==='H'?'var(--red)':lvl==='M'?'var(--orange)':'var(--green)';
    const d = days ? ` · ${days}d` : '';
    return `<span style="background:rgba(255,255,255,0.07);border-radius:4px;padding:2px 7px;font-size:11px;color:${c};font-weight:600;white-space:nowrap">${lvl==='H'?'High':lvl==='M'?'Medium':'Low'}${d}</span>`;
  };
  const progressCell = (pct, verified, tdate) => {
    if (pct === undefined || pct === null) return '<span style="color:var(--muted)">—</span>';
    const p = Number(pct)||0;
    const bc = p>=100?'var(--green)':p>=50?'var(--accent)':p>0?'var(--orange)':'rgba(255,255,255,0.15)';
    const vBadge = verified ? '<span style="font-size:10px;color:var(--green);margin-left:4px">&#10003; Verified</span>' : '';
    const ds = tdate ? `<div style="font-size:10px;color:var(--muted);margin-top:2px">Target: ${tdate}</div>` : '';
    return `<div><div style="display:flex;align-items:center;gap:4px"><div style="flex:1;min-width:60px;background:var(--surface2);border-radius:3px;height:6px;overflow:hidden"><div style="width:${p}%;background:${bc};height:100%;border-radius:3px"></div></div><span style="font-size:11px;color:var(--muted);white-space:nowrap">${p}%</span>${vBadge}</div>${ds}</div>`;
  };

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
      <td style="font-size:12px">${effortBadge(a.effort_level, a.fte_days)}</td>
      <td style="min-width:120px">${progressCell(a.completion_pct, a.verified, a.target_date)}</td>
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
            <th>Article</th><th>Beskrivelse</th><th>Status</th><th>Risiko</th><th>Hvad mangler</th><th>Konsekvens</th><th>Anbefalet Remedy</th><th>Indsats</th><th>Fremdrift</th>
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
    // Contracts-only state: no ICT register uploaded. Show analysed contracts as list + explain register value.
    const analyses = await fetch('/api/analyses').then(r => r.json()).catch(() => []);
    const contractList = Array.isArray(analyses) && analyses.length
      ? `<div class="table-wrap" style="margin-top:16px"><table style="font-size:12px">
          <thead><tr><th>Contract / Supplier</th><th>Analysis Status</th><th>Missing Requirements</th><th>Classification</th></tr></thead>
          <tbody>${analyses.map(a => `<tr style="cursor:pointer" onclick="showContractModal('${a.id||a.key||''}')">
            <td style="font-weight:500">${a.supplier_name||a.filename||'—'}</td>
            <td>${(a.overall_status||'').replace(/_/g,' ')}</td>
            <td style="font-weight:600;color:${(a.missing_count||0)>0?'var(--orange)':'var(--green)'}">${a.missing_count||0}</td>
            <td>${(a.classification||'—')}</td>
          </tr>`).join('')}</tbody>
        </table></div>`
      : '<p style="font-size:13px;color:var(--muted);margin-top:12px">No contracts analysed yet. Upload contracts in the <strong>Contracts</strong> tab.</p>';
    container.innerHTML = `
      <div class="info-box" style="margin-bottom:16px">
        <strong>Contracts uploaded — ICT Register not yet loaded.</strong>
        Your contracts have been analysed for DORA/GDPR compliance gaps. To unlock the full register reconciliation (DORA Art. 28(3) completeness proof), upload your ICT third-party register via the register endpoint. The register lets you demonstrate to supervisors that <em>every</em> provider in your register has been assessed — not just those whose contracts you uploaded.
      </div>
      <div class="section-title" style="margin-bottom:4px">Analysed Contracts — ${Array.isArray(analyses)?analyses.length:0} contract(s)</div>
      ${contractList}`;
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
        <tbody>${data.matched.map(m => '<tr style="cursor:pointer" onclick="showContractModal(\\'' + (m.analysis_key||m.analysis_id||'') + '\\')">'
          + '<td style="font-weight:500">' + m.register_entry.supplier_name + '</td>'
          + '<td><button onclick="event.stopPropagation();showContractModal(\\'' + (m.analysis_key||m.analysis_id||'') + '\\')" style="background:var(--surface2);color:var(--accent);border:1px solid var(--border);border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer">' + m.register_entry.contract_reference + ' &#8594;</button></td>'
          + '<td>' + classBadge(m.register_entry.classification) + '</td>'
          + '<td>' + statusBadge(m.overall_status) + '</td>'
          + '<td style="font-weight:600;color:' + (m.missing_count>0?'var(--orange)':'var(--green)') + '">' + m.missing_count + '</td>'
          + '<td style="color:var(--muted);font-size:12px">' + m.match_method + '</td>'
          + '</tr>').join('')}</tbody>
      </table></div>
    </div>` : ''}

    ${data.register_only && data.register_only.length ? `
    <div class="recon-section">
      <div class="section-title" style="color:var(--red)">&#9888; Register entries without contract assessment — ${data.register_only.length} gap(s)</div>
      <p style="font-size:12px;color:var(--muted);margin:4px 0 10px">These providers must be assessed under DORA Art. 30 before their contract is renewed. Trigger a contract analysis by uploading the contract in the Contracts tab.</p>
      <div class="table-wrap"><table>
        <thead><tr><th>Supplier</th><th>Contract Ref</th><th>Classification</th><th>Service</th><th>Contract Expires</th></tr></thead>
        <tbody>${data.register_only.map(e => '<tr>'
          + '<td style="font-weight:500">' + e.supplier_name + '</td>'
          + '<td><code>' + e.contract_reference + '</code></td>'
          + '<td>' + classBadge(e.classification) + '</td>'
          + '<td style="color:var(--muted);font-size:12px">' + (e.service_type||'').replace(/_/g,' ') + '</td>'
          + '<td style="font-size:12px;color:var(--orange)">' + (e.contract_end_date||'—') + '</td>'
          + '</tr>').join('')}</tbody>
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
  container.innerHTML = '<div class="loading">Loading working papers&#8230;</div>';
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
  const resultIcon = r => ({PASSED:'<span class="result-passed">✓ Pass</span>',EXCEPTION:'<span class="result-exception">⚠ Finding</span>',NOT_TESTED:'<span class="result-not-tested">— Not Tested</span>',NOT_APPLICABLE:'<span class="result-na">— N/A</span>'}[r]||r);
  const resultClass = r => r==='PASSED'?'result-passed':r==='EXCEPTION'?'result-exception':r==='NOT_APPLICABLE'?'result-na':'result-not-tested';

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
      <div class="card"><div class="label">With Findings</div><div class="value ${data.exception_count>0?'yellow':''}">${data.exception_count}</div></div>
      <div class="card"><div class="label">Open Findings</div><div class="value ${data.open_findings>0?'red':''}">${data.open_findings}</div></div>
    </div>
    <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px;font-size:11px;color:var(--muted)">
      <span><span style="color:var(--green)">●</span> Pass &nbsp; <span style="color:var(--orange)">●</span> Finding &nbsp; <span style="color:var(--muted)">●</span> Not tested / N/A &nbsp;·&nbsp; Click a row for the full working paper</span>
    </div>
    <div id="wp-heatmap" style="margin-bottom:20px"></div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th style="width:110px">Control ID</th><th>Title</th><th>Risk</th><th>Findings</th><th>Status</th>
        </tr></thead>
        <tbody>${data.work_papers.map(wp => {
          const rr = residualRiskWP(wp.inherent_risk, wp.open_finding_count);
          const rrC = residualRiskClassWP(wp.inherent_risk, wp.open_finding_count);
          const findingsCell = wp.open_finding_count > 0
            ? `<span style="color:var(--orange)">${wp.open_finding_count} open</span>${wp.finding_count > wp.open_finding_count ? ` <span style="color:var(--muted);font-size:11px">(${wp.finding_count} total)</span>` : ''}`
            : wp.finding_count > 0
              ? `<span style="color:var(--muted)">${wp.finding_count} closed</span>`
              : '<span style="color:var(--green);font-size:12px">&#10003; None</span>';
          const statusCell = wp.test_result === 'NOT_TESTED'
            ? '<span style="color:var(--muted);font-size:12px">Not tested</span>'
            : wp.test_result === 'NOT_APPLICABLE'
              ? '<span style="color:var(--muted);font-size:12px">N/A</span>'
              : wp.test_result === 'PASSED'
                ? '<span style="color:var(--green);font-size:12px">&#10003; Pass</span>'
                : '<span style="color:var(--orange);font-size:12px">&#9888; Finding</span>';
          return `<tr onclick="showWPDetail('${wp.control_id}')" style="cursor:pointer">
            <td><code style="font-size:11px">${wp.control_id}</code></td>
            <td>${wp.control_title}</td>
            <td><span class="badge ${rrC}">${rr}</span></td>
            <td>${findingsCell}</td>
            <td>${statusCell}</td>
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
  const colLabels = {PASSED:'Passed',EXCEPTION:'Finding',NOT_TESTED:'Not Tested',NOT_APPLICABLE:'N/A'};
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
    <p style="font-size:11px;color:var(--muted);margin-top:6px">${hm.total} controls total. Red cells = findings on high/critical controls requiring immediate attention.</p>
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

  const inquiryDate = new Date().toLocaleDateString('en-GB', {day:'numeric',month:'long',year:'numeric'});
  const evidenceItems = wp.expected_evidence_artifacts||[];

  // Build evidence as links with version/date annotation
  const evidenceHtml = evidenceItems.length
    ? '<ul style="margin:6px 0 0;padding-left:18px;font-size:12px">' + evidenceItems.map(e => {
        // If item contains a version marker like "v." or "version", render as link with annotation
        const vMatch = e.match(/(.+?),?\\s*(v\\.?\\s*\\d[^,]*(?:,\\s*\\d{1,2}\\s+\\w+\\s+\\d{4})?)(.*)/i);
        if (vMatch) {
          return '<li style="margin-bottom:4px"><a href="#" onclick="return false" style="color:var(--accent);text-decoration:underline">' + vMatch[1].trim() + '</a><span style="color:var(--muted);font-size:11px;margin-left:6px">(' + vMatch[2].trim() + (vMatch[3]?vMatch[3]:'') + ')</span></li>';
        }
        return '<li style="margin-bottom:4px"><a href="#" onclick="return false" style="color:var(--accent);text-decoration:underline">' + e + '</a></li>';
      }).join('') + '</ul>'
    : '<p style="font-size:12px;color:var(--muted);margin:4px 0 0">No evidence artifacts specified.</p>';

  let scenarioLabel, declarationHtml, resultLine, csLine, rrLine;
  const hasFindings = wp.findings && wp.findings.length > 0;

  if (wp.test_result === 'PASSED') {
    scenarioLabel = 'Design &amp; Implementation Review (D&amp;I) — Scenario A';
    const policyRef = wp.policy_reference || (wp.control_domain + ' Policy');
    declarationHtml =
      '<p style="margin:8px 0 6px;line-height:1.7;font-size:13px">We inspected ' + (wp.sample_count || '1') + ' item(s) from the <a href="#" onclick="return false" style="color:var(--accent);text-decoration:underline">' + policyRef + '</a> for <strong>' + wp.control_domain + '</strong>.</p>' +
      '<p style="margin:4px 0 6px;line-height:1.7;font-size:13px">Risk: <em>' + (wp.risk_statement || 'Failure of this control may result in regulatory non-compliance or operational disruption.') + '</em></p>' +
      '<p style="margin:4px 0 6px;line-height:1.7;font-size:13px">The control <strong>' + wp.control_id + ' — ' + wp.control_title + '</strong> is designed adequately and has been implemented as intended.</p>' +
      '<p style="margin:4px 0 6px;line-height:1.7;font-size:13px">We inquired with the responsible control owner on <strong>' + inquiryDate + '</strong> and confirmed the control is operating as designed. A communication log of the inquiry is available on request.</p>' +
      '<p style="margin:4px 0 6px;line-height:1.7;font-size:13px">Controls are assessed as adequate.</p>' +
      '<p style="margin:4px 0 6px;line-height:1.7;font-size:13px">Our review did not give rise to further comments.</p>';
    resultLine = '<p style="margin:8px 0 4px;font-size:13px;font-weight:600;color:var(--green)">&#10003; Pass — Control Effective</p>';
    const cs = controlStrengthWP(wp.open_finding_count);
    csLine = '<p style="margin:4px 0 4px;font-size:13px;font-weight:600;color:var(--green)">Control Strength: ' + cs + '</p>';
    rrLine = '<p style="margin:4px 0 4px;font-size:13px">Residual Risk: <strong style="color:var(--green)">Low</strong></p>';
  } else if (wp.test_result === 'EXCEPTION') {
    scenarioLabel = 'Design &amp; Implementation Review (D&amp;I) — Scenario B — Finding(s) Noted';
    const policyRef = wp.policy_reference || (wp.control_domain + ' Policy');
    declarationHtml =
      '<p style="margin:8px 0 6px;line-height:1.7;font-size:13px">We inspected ' + (wp.sample_count || '1') + ' item(s) from the <a href="#" onclick="return false" style="color:var(--accent);text-decoration:underline">' + policyRef + '</a> for <strong>' + wp.control_domain + '</strong>.</p>' +
      '<p style="margin:4px 0 6px;line-height:1.7;font-size:13px">Risk: <em>' + (wp.risk_statement || 'Failure of this control may result in regulatory non-compliance or operational disruption.') + '</em></p>' +
      '<p style="margin:4px 0 6px;line-height:1.7;font-size:13px">The control <strong>' + wp.control_id + ' — ' + wp.control_title + '</strong> was reviewed for design adequacy and implementation.</p>' +
      '<p style="margin:4px 0 6px;line-height:1.7;font-size:13px">We inquired with the responsible control owner on <strong>' + inquiryDate + '</strong>. A communication log of the inquiry is available on request.</p>' +
      '<p style="margin:4px 0 6px;line-height:1.7;font-size:13px;color:var(--orange)"><strong>Our review identified findings (see findings below).</strong></p>';
    const cs = controlStrengthWP(wp.open_finding_count);
    const isPartial = cs === 'Partially Effective';
    resultLine = '<p style="margin:8px 0 4px;font-size:13px;font-weight:600;color:var(--orange)">&#9888; ' + (isPartial ? 'Partly Effective' : 'Not Effective') + ' — Finding(s) Noted</p>';
    csLine = '<p style="margin:4px 0 4px;font-size:13px;font-weight:600;color:' + (isPartial ? 'var(--orange)' : 'var(--red)') + '">Control Strength: ' + cs + '</p>';
    if (isPartial) {
      rrLine = '<p style="margin:4px 0 4px;font-size:13px">Residual Risk: <strong style="color:var(--orange)">Medium</strong> — refer to findings below</p>';
    } else {
      rrLine = '';
    }
  } else if (wp.test_result === 'NOT_APPLICABLE') {
    scenarioLabel = 'Not Applicable to This Engagement Scope — Scenario C';
    declarationHtml =
      '<p style="margin:8px 0 6px;line-height:1.7;font-size:13px">Control <strong>' + wp.control_id + ' — ' + wp.control_title + '</strong> was reviewed for inclusion in the audit universe but determined to be <strong>not applicable</strong> to this engagement scope or the specific entity under review.</p>' +
      '<p style="margin:4px 0 6px;line-height:1.7;font-size:13px">No testing was performed.</p>';
    resultLine = '<p style="margin:8px 0 4px;font-size:13px;font-weight:600;color:var(--muted)">— Not Applicable</p>';
    csLine = '<p style="margin:4px 0 4px;font-size:13px;color:var(--muted)">Control Strength: N/A</p>';
    rrLine = '';
  } else {
    scenarioLabel = 'Scoped In — Not Tested This Period — Scenario D';
    declarationHtml =
      '<p style="margin:8px 0 6px;line-height:1.7;font-size:13px">Control <strong>' + wp.control_id + ' — ' + wp.control_title + '</strong> is included in the audit universe but was <strong>not tested</strong> in this cycle based on a risk-based deferral decision.</p>' +
      '<p style="margin:4px 0 6px;line-height:1.7;font-size:13px">This control should be prioritised in the next audit cycle.</p>';
    resultLine = '<p style="margin:8px 0 4px;font-size:13px;font-weight:600;color:var(--muted)">— Not Tested This Period</p>';
    csLine = '<p style="margin:4px 0 4px;font-size:13px;color:var(--muted)">Control Strength: N/A</p>';
    rrLine = '';
  }

  panel.innerHTML = `
    <h3><code>${wp.control_id}</code> — ${wp.control_title}</h3>
    <p style="color:var(--muted);font-size:12px;margin:6px 0 16px">${wp.control_domain} · Inherent Risk: <strong style="color:${({'CRITICAL':'var(--red)','HIGH':'var(--orange)','MEDIUM':'var(--yellow)','LOW':'var(--green)'}[wp.inherent_risk]||'var(--muted)')}">${wp.inherent_risk}</strong></p>

    <div class="wp-text-block" style="margin-bottom:16px">
      <div class="wp-declaration-label">${scenarioLabel}</div>
      ${declarationHtml}
      <div style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);margin-bottom:4px;margin-top:10px">Evidence Reviewed</div>
      ${evidenceHtml}
      <div style="margin-top:12px;border-top:1px solid var(--border);padding-top:10px">
        ${resultLine}
        ${csLine}
        ${rrLine}
        ${hasFindings ? '<p style="margin:4px 0 4px;font-size:12px;color:var(--orange)">&#8594; Refer to findings noted below.</p>' : ''}
      </div>
    </div>

    ${wp.audit_objective ? `<div style="margin-bottom:12px"><span style="font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)">Audit Procedure</span><p style="font-size:13px;margin:4px 0 0">${wp.audit_objective}</p></div>` : ''}

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

const DEMO_ORG_STRUCTURE = [
  { process: 'Information Security', subprocesses: [
    {
      name: 'Identity & Access Management',
      iso_controls: ['ISO 27001 A.9.1 — Access Control Policy','ISO 27001 A.9.2 — User Access Management'],
      critical_systems: ['Azure AD / Entra ID','CyberArk PAM','Okta SSO'],
      audit_objective: 'Confirm that access to critical systems is granted based on least-privilege and reviewed quarterly.',
      audit_action: 'Inspect access provisioning records; re-perform quarterly access review; test segregation of duties for privileged accounts.',
      risk: 'Unauthorised access to core banking systems leading to data breach or fraud.',
      controls: ['AU01','AU02'],
    },
    {
      name: 'Data Protection & Encryption',
      iso_controls: ['ISO 27001 A.10.1 — Cryptographic Controls','ISO 27001 A.8.2 — Information Classification'],
      critical_systems: ['Varonis DLP','Azure Information Protection','BitLocker'],
      audit_objective: 'Verify that data-at-rest and data-in-transit are encrypted using approved algorithms and that keys are rotated per policy.',
      audit_action: 'Review encryption configuration; test key rotation logs; inspect DLP policy coverage and incident log.',
      risk: 'Unencrypted sensitive data exposure in transit or at rest, leading to regulatory breach under GDPR Art. 32.',
      controls: ['AU03'],
    },
    {
      name: 'Vulnerability Management',
      iso_controls: ['ISO 27001 A.12.6 — Technical Vulnerability Management','ISO 27001 A.14.2 — Security in Development'],
      critical_systems: ['Tenable.io','Qualys','Microsoft Defender for Endpoint'],
      audit_objective: 'Confirm that critical vulnerabilities are remediated within SLA and that patch compliance is monitored.',
      audit_action: 'Extract open vulnerability report; verify patch SLA adherence rate; test escalation process for critical CVEs.',
      risk: 'Exploitation of known vulnerabilities leading to system compromise or operational disruption.',
      controls: ['AU04','AU05'],
    },
  ]},
  { process: 'ICT Third-Party Risk', subprocesses: [
    {
      name: 'Supplier Onboarding & Due Diligence',
      iso_controls: ['DORA Art. 28 — ICT Third-Party Risk Policy','ISO 27001 A.15.1 — Supplier Relationships'],
      critical_systems: ['ServiceNow GRC','Prevalent TPRM'],
      audit_objective: 'Confirm that all new ICT suppliers undergo risk-based due diligence before onboarding and are registered.',
      audit_action: 'Sample 5 supplier onboardings; verify due diligence evidence; test register completeness against contract inventory.',
      risk: 'Unvetted suppliers introducing operational or regulatory risk into the ICT supply chain.',
      controls: ['NNS-NET-2021-001','NNS-NET-2021-002'],
    },
    {
      name: 'Contract Governance (DORA Art. 30)',
      iso_controls: ['DORA Art. 30 — Contractual Provisions','ISO 27001 A.15.2 — Supplier Service Management'],
      critical_systems: ['DocuSign CLM','SharePoint Contract Repository'],
      audit_objective: 'Verify that all critical/important ICT contracts include the mandatory DORA Art. 30 clauses.',
      audit_action: 'Review contract gap analysis output; test remediation of identified gaps; inspect contract register for classification accuracy.',
      risk: 'Missing contractual safeguards expose the entity to supervisory sanctions and unmanaged ICT concentration risk.',
      controls: ['NNS-NET-2021-003','NNS-NET-2021-004','NNS-NET-2021-005'],
    },
    {
      name: 'Exit Strategy & Concentration Risk',
      iso_controls: ['DORA Art. 28(8) — Exit Strategies','DORA Art. 29 — Concentration Risk'],
      critical_systems: ['ServiceNow GRC','Internal BCMS'],
      audit_objective: 'Confirm documented and tested exit plans for each critical provider, and that concentration risk is assessed annually.',
      audit_action: 'Inspect exit strategy documentation; test tabletop exercise evidence; review concentration risk register.',
      risk: 'Inability to exit a critical supplier without service disruption, increasing systemic and regulatory risk.',
      controls: ['NNS-NET-2021-006','NNS-NET-2021-007'],
    },
  ]},
  { process: 'Operational Resilience', subprocesses: [
    {
      name: 'Business Continuity & Disaster Recovery',
      iso_controls: ['ISO 27001 A.17.1 — Business Continuity Management','DORA Art. 11 — ICT Business Continuity'],
      critical_systems: ['Fusion Framework','Everbridge','Veeam Backup'],
      audit_objective: 'Verify that BCP and DR plans are current, tested annually, and recovery time objectives are achievable.',
      audit_action: 'Review BCP documentation currency; inspect DR test report; validate RTO/RPO against system criticality register.',
      risk: 'Extended outage of critical systems during a disruption event due to untested or outdated recovery plans.',
      controls: ['AU07'],
    },
    {
      name: 'Incident Response & Recovery',
      iso_controls: ['ISO 27001 A.16.1 — Incident Management','DORA Art. 17 — ICT-Related Incident Management'],
      critical_systems: ['PagerDuty','Splunk SIEM','ServiceNow IRM'],
      audit_objective: 'Confirm that ICT incidents are detected, classified, reported and resolved within defined SLAs.',
      audit_action: 'Sample 10 incident records; verify classification accuracy; test escalation and notification timelines; check DORA reportability assessment.',
      risk: 'Delayed or mis-classified incidents leading to regulatory reporting failures and extended operational impact.',
      controls: [],
    },
  ]},
  { process: 'Data Privacy & GDPR', subprocesses: [
    {
      name: 'Article 28 Processor Agreements',
      iso_controls: ['GDPR Art. 28 — Controller-Processor Contracts','GDPR Art. 32 — Security of Processing'],
      critical_systems: ['OneTrust DPA Manager','DocuSign'],
      audit_objective: 'Verify that all data processors have a signed, compliant DPA in place covering all mandatory Art. 28 clauses.',
      audit_action: 'Cross-reference processor inventory against signed DPAs; test completeness of mandatory clauses; inspect sub-processor disclosure records.',
      risk: 'Processing personal data without a compliant DPA exposes the controller to GDPR enforcement and fines up to 2% of global turnover.',
      controls: [],
    },
    {
      name: 'Data Subject Rights (DSR)',
      iso_controls: ['GDPR Art. 15–22 — Data Subject Rights','ISO 27001 A.18.1 — Compliance with Legal Requirements'],
      critical_systems: ['OneTrust Privacy Portal','Salesforce Service Cloud'],
      audit_objective: 'Confirm that DSRs (access, erasure, portability) are fulfilled within the 30-day statutory deadline and documented.',
      audit_action: 'Sample 15 DSR requests; verify response timelines; test completeness of erasure execution; inspect exception log.',
      risk: 'Failure to respond to DSRs within legal timelines risks supervisory investigation and reputational harm.',
      controls: [],
    },
  ]},
];

async function loadAuditIntelligence() {
  const container = document.getElementById('audit-content');
  container.innerHTML = '<div class="loading">Loading audit intelligence&#8230;</div>';
  let wpData = { work_papers: [], engagement_id: '—', organisation: 'Demo Organisation', scope: 'Full Audit Universe', total_controls: 0, tested_count: 0, passed_count: 0, exception_count: 0, open_findings: 0 };
  try {
    const res = await fetch('/api/workpapers');
    const json = await res.json();
    if (!json.message) wpData = json;
  } catch(e) {}

  const wps = wpData.work_papers || [];
  const openFindings = wps.flatMap(w => (w.findings||[]).filter(f => f.status === 'open'));
  const sevColor = s => ({CRITICAL:'var(--red)',HIGH:'var(--orange)',MEDIUM:'var(--yellow)',LOW:'var(--green)'}[s]||'var(--muted)');
  const wpByCtrl = {};
  for (const wp of wps) wpByCtrl[wp.control_id] = wp;

  // --- Info box ---
  const infoBox = '<div class="info-box">'
    + '<div style="margin-bottom:6px"><strong style="color:var(--accent)">Agent 4 — Internal Audit &amp; Board Reporting:</strong><br>'
    + 'Covers the full internal audit cycle — universe mapping, risk assessment, control testing, draft audit reports, and board intelligence. Framework: ISO 27001, ISAE 3402, IIA Standards.</div>'
    + '<div><strong style="color:var(--muted)">Agent 2 — Supplier Assurance (see Working Papers tab):</strong><br>'
    + 'Handles DORA Art. 30 ICT third-party contract testing. The two agents share the same working paper data but serve distinct governance audiences.</div>'
    + '</div>';

  // --- Org process mapping accordion (Business Process → Sub-process → ISO Control + Critical System + Audit Objective + Audit Action + Risk) ---
  const DEFAULT_INHERENT_RISK = {
    'Identity & Access Management': 'High',
    'Data Protection & Encryption': 'Medium',
    'Vulnerability Management': 'High',
    'Supplier Onboarding & Due Diligence': 'Medium',
    'Contract Governance (DORA Art. 30)': 'High',
    'Exit Strategy & Concentration Risk': 'High',
    'Business Continuity & Disaster Recovery': 'Medium',
    'Incident Response & Recovery': 'Medium',
    'Article 28 Processor Agreements': 'Medium',
    'Data Subject Rights (DSR)': 'Low',
  };
  const PROC_RISK_RATING = {
    'Information Security': 'High',
    'ICT Third-Party Risk': 'High',
    'Operational Resilience': 'Medium',
    'Data Privacy & GDPR': 'Medium',
  };
  const riskBadge = r => r === 'High'
    ? '<span style="background:rgba(248,113,113,0.2);color:var(--red);border-radius:4px;padding:2px 7px;font-size:11px;font-weight:600;white-space:nowrap">High</span>'
    : r === 'Medium'
    ? '<span style="background:rgba(251,146,60,0.2);color:var(--orange);border-radius:4px;padding:2px 7px;font-size:11px;font-weight:600;white-space:nowrap">Medium</span>'
    : '<span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 7px;font-size:11px;font-weight:600;white-space:nowrap">Low</span>';
  const auditTypeForRiskYear = (risk, year) => {
    if (risk === 'High') return 'Full Audit';
    if (risk === 'Medium') return year === 2 ? 'Follow-up Audit' : 'Full Audit';
    return 'DI Review';
  };

  let accordionHtml = '<div class="section-title" style="margin-bottom:8px">Business Process &amp; Audit Scope Mapping</div>';
  accordionHtml += '<p style="font-size:12px;color:var(--muted);margin:0 0 12px">Business Process &#8594; Sub-process &#8594; ISO Control / Regulation &#8594; Critical System &#8594; Audit Objective &amp; Action &#8594; Risk. Click a process to expand.</p>';
  accordionHtml += '<div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:12px">'
    + '<label style="font-size:12px;color:var(--muted);white-space:nowrap">Risk class:</label>'
    + '<select id="filter-risk" onchange="applyAuditFilters()" style="font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;padding:3px 8px;color:var(--text)">'
    + '<option value="">All</option><option value="High">High</option><option value="Medium">Medium</option><option value="Low">Low</option>'
    + '</select>'
    + '<label style="font-size:12px;color:var(--muted);white-space:nowrap;margin-left:8px">Audit cycle year:</label>'
    + '<select id="filter-year" onchange="applyAuditFilters()" style="font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;padding:3px 8px;color:var(--text)">'
    + '<option value="">All years</option><option value="1">Year 1</option><option value="2">Year 2</option><option value="3">Year 3</option>'
    + '</select>'
    + '</div>';
  accordionHtml += '<div class="proc-accordion">';
  DEMO_ORG_STRUCTURE.forEach((proc, pi) => {
    const domainId = 'proc-' + pi;
    const mappedWps = proc.subprocesses.flatMap(sp => (sp.controls||[]).map(c => wpByCtrl[c]).filter(Boolean));
    const findingCount = mappedWps.filter(w => w.test_result === 'EXCEPTION').length;
    const passCount = mappedWps.filter(w => w.test_result === 'PASSED').length;
    const procRisk = PROC_RISK_RATING[proc.process] || 'Medium';
    const statusChip = findingCount ? '<span style="font-size:11px;color:var(--orange);margin-left:8px">&#9888; ' + findingCount + ' finding(s)</span>'
      : passCount ? '<span style="font-size:11px;color:var(--green);margin-left:8px">&#10003; ' + passCount + ' tested</span>'
      : '';
    accordionHtml += '<div class="proc-header" onclick="const b=document.getElementById(\\'' + domainId + '\\');b.classList.toggle(\\'open\\')">'
      + '<span style="font-size:13px;font-weight:600">' + proc.process + statusChip + '&nbsp;&nbsp;' + riskBadge(procRisk) + '</span>'
      + '<span style="font-size:11px;color:var(--muted)">' + proc.subprocesses.length + ' sub-process(es) &#9660;</span>'
      + '</div>';
    accordionHtml += '<div id="' + domainId + '" class="proc-body">';
    accordionHtml += '<table style="width:100%;border-collapse:collapse;font-size:12px">'
      + '<thead><tr style="border-bottom:1px solid var(--border)">'
      + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-weight:600;white-space:nowrap">Sub-process</th>'
      + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-weight:600">ISO Control / Regulation</th>'
      + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-weight:600;white-space:nowrap">Critical System</th>'
      + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-weight:600">Audit Objective</th>'
      + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-weight:600">Audit Action</th>'
      + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-weight:600">Risk</th>'
      + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-weight:600;white-space:nowrap">Inherent Risk</th>'
      + '<th style="text-align:left;padding:6px 8px;color:var(--muted);font-weight:600;white-space:nowrap">Audit Type</th>'
      + '</tr></thead><tbody>';
    proc.subprocesses.forEach(sp => {
      const isoHtml = (sp.iso_controls||[]).map(c => '<div style="line-height:1.5">' + c + '</div>').join('');
      const sysHtml = (sp.critical_systems||[]).map(s => '<span style="font-size:11px;background:var(--surface2);border:1px solid var(--border);border-radius:3px;padding:1px 5px;display:inline-block;margin:1px 2px 1px 0">' + s + '</span>').join('');
      const ctrlWps = (sp.controls||[]).map(c => wpByCtrl[c]).filter(Boolean);
      let wpStatusHtml;
      if (sp.controls && sp.controls.length > 0) {
        const hasFindings = ctrlWps.some(w => w.test_result === 'EXCEPTION');
        const allPassed = ctrlWps.length > 0 && ctrlWps.every(w => w.test_result === 'PASSED');
        wpStatusHtml = sp.controls.map(ctrlId => {
          const wp = wpByCtrl[ctrlId];
          const badgeCls = !wp ? 'ctrl-badge-mapped' : wp.test_result === 'PASSED' ? 'ctrl-badge-passed' : wp.test_result === 'EXCEPTION' ? 'ctrl-badge-exception' : 'ctrl-badge-not-tested';
          return '<span class="ctrl-badge ' + badgeCls + '" onclick="' + (wp ? 'showWPDetail(\\'' + ctrlId + '\\')' : '') + '" style="' + (wp ? 'cursor:pointer;' : '') + 'display:inline-block;margin:1px">' + ctrlId + '</span>';
        }).join('');
      } else {
        wpStatusHtml = '<span style="font-size:11px;color:var(--muted)">Scope TBD</span>';
      }
      const spInherent = DEFAULT_INHERENT_RISK[sp.name] || 'Medium';
      const spAuditType = auditTypeForRiskYear(spInherent, 1);
      const rowId = 'row-' + pi + '-' + proc.subprocesses.indexOf(sp);
      accordionHtml += '<tr id="' + rowId + '" data-inherent="' + spInherent + '" style="border-bottom:1px solid var(--border);vertical-align:top">'
        + '<td style="padding:8px;font-weight:600;color:var(--text);white-space:nowrap">' + sp.name + '</td>'
        + '<td style="padding:8px;line-height:1.5">' + isoHtml + '</td>'
        + '<td style="padding:8px">' + sysHtml + '</td>'
        + '<td style="padding:8px;color:var(--muted)">' + (sp.audit_objective||'') + '</td>'
        + '<td style="padding:8px;color:var(--muted)">' + (sp.audit_action||'') + '</td>'
        + '<td style="padding:8px;color:var(--red)">' + (sp.risk||'') + '</td>'
        + '<td style="padding:8px">' + riskBadge(spInherent) + '</td>'
        + '<td style="padding:8px"><select class="audit-type-sel" style="font-size:11px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;padding:2px 6px;color:var(--text)">'
        + ['Full Audit','Follow-up Audit','DI Review'].map(t => '<option value="' + t + '"' + (t === spAuditType ? ' selected' : '') + '>' + t + '</option>').join('')
        + '</select></td>'
        + '</tr>';
    });
    accordionHtml += '</tbody></table>';
    accordionHtml += '</div>';
  });
  accordionHtml += '</div>';

  // --- Draft Audit Reports (open DAR modal on click) ---
  const draftReports = [
    {id:'DAR-2025-001', title:'ISO 27001 Information Security Controls Assessment — Q4 2025', scope:'AU01–AU05 (Identity, Access, Data Protection)', status:'Draft', date:'2025-12-15', findings:2, auditor:'Internal Audit', summary:'Controls AU01–AU05 were tested for the Q4 2025 period. Two exceptions were identified in access review and vulnerability patch timelines. Management has acknowledged findings and agreed remediation plans.'},
    {id:'DAR-2025-002', title:'DORA Art. 30 ICT Third-Party Contract Gap Analysis — FY 2025', scope:'5 ICT contracts assessed against DORA Art. 30 requirements', status:'Draft', date:'2025-12-20', findings:1, auditor:'IT Audit', summary:'Five ICT contracts were assessed for compliance with DORA Art. 30. One exception was identified relating to missing exit strategy documentation for a critical provider.'},
    {id:'DAR-2025-003', title:'BCP / DR Operational Resilience Review — Q4 2025', scope:'AU07 (Business Continuity Planning)', status:'In Review', date:'2025-11-30', findings:0, auditor:'Internal Audit', summary:'Business continuity and disaster recovery controls were reviewed. No exceptions identified. BCP documentation is current and DR test was completed successfully.'},
  ];
  window._darReports = draftReports;
  const statusBadgeRpt = s => s === 'Draft' ? '<span style="background:rgba(124,106,255,0.2);color:var(--accent);border-radius:4px;padding:2px 8px;font-size:11px">Draft</span>'
    : s === 'In Review' ? '<span style="background:rgba(251,191,36,0.2);color:var(--yellow);border-radius:4px;padding:2px 8px;font-size:11px">In Review</span>'
    : '<span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 8px;font-size:11px">Finalised</span>';

  // 3-Year audit methodology
  const methodologyHtml = '<div style="margin-bottom:24px"><div class="section-title" style="margin-bottom:4px">3-Year Risk-Based Audit Methodology</div>'
    + '<p style="font-size:12px;color:var(--muted);margin:0 0 12px">Annual audit cadence determined by risk rating. Staffing: 3 FTE Internal Audit (1 Lead Auditor + 2 Senior Auditors). Full reviews are substantive testing; Follow-up reviews confirm prior-year remediation; D&amp;I reviews confirm controls are designed and implemented.</p>'
    + '<div class="table-wrap"><table style="font-size:12px"><thead><tr><th>Risk Rating</th><th>Audit Type</th><th>Year 1</th><th>Year 2</th><th>Year 3</th><th>Total Hours/Cycle</th></tr></thead><tbody>'
    + '<tr><td style="color:var(--red);font-weight:600">High</td><td>Full audit annually</td><td style="text-align:center">Full Audit (240h = 30d)</td><td style="text-align:center">Full Audit (240h)</td><td style="text-align:center">Full Audit (240h)</td><td style="text-align:center;font-weight:600">720h</td></tr>'
    + '<tr><td style="color:var(--orange);font-weight:600">Medium</td><td>Follow-up every 2 yrs</td><td style="text-align:center">Full Audit (160h = 20d)</td><td style="text-align:center">Follow-up (40h)</td><td style="text-align:center">Full Audit (160h)</td><td style="text-align:center;font-weight:600">360h</td></tr>'
    + '<tr><td style="color:var(--green);font-weight:600">Low</td><td>D&amp;I check every 3 yrs, spread evenly</td><td style="text-align:center">D&amp;I (13h)</td><td style="text-align:center">D&amp;I (14h)</td><td style="text-align:center">D&amp;I (13h = 5d total)</td><td style="text-align:center;font-weight:600">40h</td></tr>'
    + '</tbody></table></div>'
    + '<div class="info-box" style="margin-top:10px;font-size:12px"><strong>Hour budgets:</strong> High risk = 240 hrs (1.5 months) full audit each year. Medium risk = 160 hrs (1 month) full audit + 40 hrs follow-up every other year. Low risk = 40 hrs (5 days) D&amp;I spread over 3 years. <strong>Staffing:</strong> 3 FTE Internal Audit — Lead Auditor owns engagement quality and board reporting; two Senior Auditors conduct fieldwork, working paper preparation and finding management. External co-sourcing may supplement for specialist technical reviews.</div>'
    + '</div>';

  const reportsHtml = '';

  // --- P1-P4 Finding Priority Framework ---
  const p1p4Html = '<div style="margin-bottom:24px"><div class="section-title" style="margin-bottom:8px">Finding Priority Framework — P1 to P4</div>'
    + '<p style="font-size:12px;color:var(--muted);margin:0 0 12px">IIA-aligned finding severity model. Priority assigned at time of audit conclusion. All P1 findings trigger automatic Board escalation and 3-month remediation deadline.</p>'
    + '<div class="table-wrap"><table style="font-size:12px"><thead><tr><th>Priority</th><th>Severity</th><th>Escalation</th><th>Deadline</th><th>Example</th></tr></thead><tbody>'
    + '<tr><td><span style="color:var(--red);font-weight:700;font-size:13px">P1</span></td><td style="color:var(--red);font-weight:600">Critical</td><td>Board / CEO</td><td>&#8804; 3 months</td><td style="color:var(--muted)">Regulatory breach; material financial exposure; audit rights completely missing from critical ICT contract</td></tr>'
    + '<tr><td><span style="color:var(--orange);font-weight:700;font-size:13px">P2</span></td><td style="color:var(--orange);font-weight:600">Relevant</td><td>ExCo / CRO</td><td>&#8804; 6 months</td><td style="color:var(--muted)">Operational risk increase; missing DORA Art. 30 clause in critical supplier; incomplete BCP test coverage</td></tr>'
    + '<tr><td><span style="color:var(--yellow);font-weight:700;font-size:13px">P3</span></td><td style="color:var(--yellow);font-weight:600">Medium</td><td>Process Owner</td><td>&#8804; 12 months</td><td style="color:var(--muted)">Process control gap; partial SLA documentation; GDPR retention schedule incomplete</td></tr>'
    + '<tr><td><span style="color:var(--green);font-weight:700;font-size:13px">P4</span></td><td style="color:var(--green);font-weight:600">Low</td><td>Process Owner</td><td>Annual review</td><td style="color:var(--muted)">Best practice improvement; minor documentation gap; low-risk observation</td></tr>'
    + '</tbody></table></div></div>';

  // --- Nexi Audit Conclusion / ICS Rating Model ---
  const nexiHtml = '<div style="margin-bottom:24px"><div class="section-title" style="margin-bottom:8px">Nexi ICS Rating Model — Audit Conclusion Framework</div>'
    + '<p style="font-size:12px;color:var(--muted);margin:0 0 12px">Each finalised audit report carries an ICS (Internal Control System) conclusion. The rolling average over 4 consecutive audits on the same process updates the inherent risk rating in the audit universe.</p>'
    + '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin-bottom:12px">'
    + '<div style="background:rgba(34,211,238,0.1);border:1px solid rgba(34,211,238,0.3);border-radius:8px;padding:12px"><div style="color:#22d3ee;font-weight:700;font-size:14px;margin-bottom:4px">Good — ICS Fully Satisfactory</div><div style="font-size:12px;color:var(--muted)">No material findings. Controls operating effectively. Risk level may decrease one tier at next annual review.</div></div>'
    + '<div style="background:rgba(74,222,128,0.1);border:1px solid rgba(74,222,128,0.3);border-radius:8px;padding:12px"><div style="color:var(--green);font-weight:700;font-size:14px;margin-bottom:4px">Satisfactory — Slight Positive</div><div style="font-size:12px;color:var(--muted)">Minor observations only (P3–P4). ICS broadly effective. Risk level unchanged. Standard follow-up cycle applies.</div></div>'
    + '<div style="background:rgba(251,191,36,0.1);border:1px solid rgba(251,191,36,0.3);border-radius:8px;padding:12px"><div style="color:var(--yellow);font-weight:700;font-size:14px;margin-bottom:4px">Unsatisfactory — Risk Increases</div><div style="font-size:12px;color:var(--muted)">P1–P2 findings present. Control gaps increase operational risk. Risk level escalated one tier. Follow-up audit within 12 months.</div></div>'
    + '<div style="background:rgba(248,113,113,0.1);border:1px solid rgba(248,113,113,0.3);border-radius:8px;padding:12px"><div style="color:var(--red);font-weight:700;font-size:14px;margin-bottom:4px">Poor — Significant Risk Increase</div><div style="font-size:12px;color:var(--muted)">Multiple P1 findings. ICS materially deficient. Risk level escalated to High. Mandatory re-audit within 6 months. Board escalation required.</div></div>'
    + '</div>'
    + '<div class="info-box" style="font-size:12px"><strong>Rolling average rule:</strong> When the same process receives 4 consecutive audit conclusions, their average rating feeds into the audit universe risk model. Good × 4 → risk reduced; Poor × 2 or more in 4 → risk upgraded to High and annual full audit triggered regardless of original risk tier.</div>'
    + '</div>';

  // --- FTE Capacity Planning ---
  const ftePlanningHtml = '<div style="margin-bottom:24px"><div class="section-title" style="margin-bottom:8px">FTE Capacity Planning — Internal Audit</div>'
    + '<p style="font-size:12px;color:var(--muted);margin:0 0 12px">3 FTE Internal Audit: 1 Lead Auditor + 2 Senior Auditors. 220 working days/year per FTE. 70% utilisation = 154 chargeable days / 1,232 hrs per FTE. Total team capacity: 3,696 hrs/year.</p>'
    + '<div class="table-wrap" style="margin-bottom:12px"><table style="font-size:12px"><thead><tr><th>Sub-process</th><th>Risk</th><th>Year 1 (hrs)</th><th>Year 2 (hrs)</th><th>Year 3 (hrs)</th><th>3-yr Total</th></tr></thead><tbody>'
    + '<tr><td>Identity &amp; Access Management</td><td><span style="color:var(--red);font-weight:600">High</span></td><td>240</td><td>240</td><td>240</td><td style="font-weight:600">720</td></tr>'
    + '<tr><td>Data Protection &amp; Encryption</td><td><span style="color:var(--orange);font-weight:600">Medium</span></td><td>160</td><td>40</td><td>160</td><td style="font-weight:600">360</td></tr>'
    + '<tr><td>Vulnerability Management</td><td><span style="color:var(--red);font-weight:600">High</span></td><td>240</td><td>240</td><td>240</td><td style="font-weight:600">720</td></tr>'
    + '<tr><td>Supplier Onboarding &amp; Due Diligence</td><td><span style="color:var(--orange);font-weight:600">Medium</span></td><td>160</td><td>40</td><td>160</td><td style="font-weight:600">360</td></tr>'
    + '<tr><td>Contract Governance (DORA Art. 30)</td><td><span style="color:var(--red);font-weight:600">High</span></td><td>240</td><td>240</td><td>240</td><td style="font-weight:600">720</td></tr>'
    + '<tr><td>Exit Strategy &amp; Concentration Risk</td><td><span style="color:var(--red);font-weight:600">High</span></td><td>240</td><td>240</td><td>240</td><td style="font-weight:600">720</td></tr>'
    + '<tr><td>Business Continuity &amp; Disaster Recovery</td><td><span style="color:var(--orange);font-weight:600">Medium</span></td><td>160</td><td>40</td><td>160</td><td style="font-weight:600">360</td></tr>'
    + '<tr><td>Incident Response &amp; Recovery</td><td><span style="color:var(--orange);font-weight:600">Medium</span></td><td>160</td><td>40</td><td>160</td><td style="font-weight:600">360</td></tr>'
    + '<tr><td>Art. 28 Processor Agreements (GDPR)</td><td><span style="color:var(--orange);font-weight:600">Medium</span></td><td>160</td><td>40</td><td>160</td><td style="font-weight:600">360</td></tr>'
    + '<tr><td>Data Subject Rights (DSR)</td><td><span style="color:var(--green);font-weight:600">Low</span></td><td>13</td><td>14</td><td>13</td><td style="font-weight:600">40</td></tr>'
    + '<tr style="border-top:2px solid var(--border);font-weight:700"><td>Total hours / year</td><td></td><td style="color:var(--accent)">1,773 hrs</td><td style="color:var(--muted)">1,174 hrs</td><td style="color:var(--accent)">1,773 hrs</td><td>4,720 hrs</td></tr>'
    + '<tr style="font-size:11px;color:var(--muted)"><td>FTE utilisation (÷1,232 hrs)</td><td></td><td>1.44 FTE</td><td>0.95 FTE</td><td>1.44 FTE</td><td>avg 1.28 FTE</td></tr>'
    + '</tbody></table></div>'
    + '<div class="info-box" style="font-size:12px"><strong>Surplus capacity:</strong> Year 2 leaves ~1.5 FTE capacity for ad-hoc assignments, special investigations, and regulatory liaison. Lead Auditor time is allocated ~25% to board reporting, quality review, and IIA standards compliance. External co-sourcing recommended for specialist IT audit areas (penetration testing, cloud security) at ~15 consultant days/year.</div>'
    + '</div>';

  // --- Open Findings (compact) ---
  let findingsHtml = '';
  if (openFindings.length) {
    findingsHtml = '<div style="margin-bottom:24px"><div class="section-title" style="color:var(--orange);margin-bottom:8px">&#9888; Open Findings — ' + openFindings.length + ' item(s) pending resolution</div>';
    findingsHtml += '<div class="table-wrap"><table><thead><tr><th>Finding ID</th><th>Title</th><th>Severity</th><th>Owner</th><th>Deadline</th></tr></thead><tbody>';
    findingsHtml += openFindings.map(f =>
      '<tr><td><code style="font-size:11px">' + f.finding_id + '</code></td>'
      + '<td style="font-size:12px">' + f.title + '</td>'
      + '<td><span style="color:' + sevColor(f.severity) + ';font-weight:600;font-size:12px">' + f.severity + '</span></td>'
      + '<td style="font-size:12px;color:var(--muted)">' + (f.owner||'—') + '</td>'
      + '<td style="font-size:12px;color:var(--yellow)">' + (f.target_remediation_date||'TBD') + '</td></tr>'
    ).join('');
    findingsHtml += '</tbody></table></div></div>';
  }

  // --- 3×3 Risk Matrix (Impact × Likelihood) ---
  const RISK_MATRIX_DATA = [
    { label: 'ICT Contract Gaps (DORA Art.30)', impact: 3, likelihood: 3 },
    { label: 'Access Control Gaps (IAM)', impact: 3, likelihood: 2 },
    { label: 'Exit Strategy Missing', impact: 3, likelihood: 2 },
    { label: 'Patch / Vuln Management Delays', impact: 2, likelihood: 3 },
    { label: 'BCP / DR Not Fully Tested', impact: 3, likelihood: 1 },
    { label: 'DPA Art.28 Gaps (GDPR)', impact: 2, likelihood: 2 },
    { label: 'SLA / Sub-contractor Clause Missing', impact: 2, likelihood: 2 },
    { label: 'User Training Compliance', impact: 1, likelihood: 2 },
    { label: 'Encryption Key Rotation', impact: 2, likelihood: 1 },
    { label: 'DSR Response Logging', impact: 1, likelihood: 1 },
    { label: 'Register Maintenance Lag', impact: 1, likelihood: 2 },
  ];
  function buildRiskMatrix(items) {
    const cells = {};
    for (let i = 1; i <= 3; i++) for (let l = 1; l <= 3; l++) cells[i+','+l] = [];
    items.forEach(it => cells[it.impact+','+it.likelihood].push(it));
    const bgCell = (imp, lik) => { const s=imp*lik; return s>=6?'rgba(248,113,113,0.18)':s>=3?'rgba(251,146,60,0.18)':'rgba(74,222,128,0.14)'; };
    const borderCell = (imp, lik) => { const s=imp*lik; return s>=6?'#f87171':s>=3?'#fb923c':'#4ade80'; };
    let html = '<div style="margin-bottom:24px">';
    html += '<div class="section-title" style="margin-bottom:4px">Risk Matrix — Impact &#215; Likelihood (3&#215;3)</div>';
    html += '<p style="font-size:12px;color:var(--muted);margin:0 0 12px">Red = High risk (score 6–9) &nbsp;|&nbsp; Orange = Medium risk (3–5) &nbsp;|&nbsp; Green = Low risk (1–2). Board/audit ready.</p>';
    html += '<div style="display:grid;grid-template-columns:60px repeat(3,1fr);gap:4px;max-width:720px">';
    html += '<div style="font-size:10px;color:var(--muted);display:flex;align-items:flex-end;padding-bottom:4px;line-height:1.3">Impact &#8595;<br>Likelihood &#8594;</div>';
    ['Low (1)','Medium (2)','High (3)'].forEach(l => {
      html += '<div style="font-size:11px;color:var(--muted);text-align:center;padding:4px 2px;border-bottom:1px solid var(--border)">' + l + '</div>';
    });
    for (let imp = 3; imp >= 1; imp--) {
      const impLabel = imp===3?'High (3)':imp===2?'Med (2)':'Low (1)';
      html += '<div style="font-size:10px;color:var(--muted);display:flex;align-items:center;justify-content:flex-end;padding-right:6px;text-align:right">' + impLabel + '</div>';
      for (let lik = 1; lik <= 3; lik++) {
        const its = cells[imp+','+lik] || [];
        html += '<div style="background:' + bgCell(imp,lik) + ';border:1px solid ' + borderCell(imp,lik) + ';border-radius:6px;padding:6px;min-height:70px;font-size:10px;line-height:1.5">';
        its.forEach(it => { html += '<div style="color:var(--text)">&#8226; ' + it.label + '</div>'; });
        if (!its.length) html += '<div style="color:var(--muted)">—</div>';
        html += '</div>';
      }
    }
    html += '</div></div>';
    return html;
  }
  const riskMatrixHtml = buildRiskMatrix(RISK_MATRIX_DATA);

  container.innerHTML = '<div style="margin-bottom:4px"><span style="color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em">Engagement:</span> <code>' + wpData.engagement_id + '</code> — ' + wpData.organisation + ' <span style="color:var(--muted);font-size:12px">/ ' + wpData.scope + '</span></div>'
    + '<div class="summary-cards" style="margin:16px 0">'
    + '<div class="card"><div class="label">Audit Universe</div><div class="value">' + wpData.total_controls + '</div></div>'
    + '<div class="card"><div class="label">Tested</div><div class="value">' + wpData.tested_count + '</div></div>'
    + '<div class="card"><div class="label">Passed</div><div class="value green">' + wpData.passed_count + '</div></div>'
    + '<div class="card"><div class="label">With Findings</div><div class="value ' + (wpData.exception_count>0?'yellow':'') + '">' + wpData.exception_count + '</div></div>'
    + '<div class="card"><div class="label">Open Findings</div><div class="value ' + (wpData.open_findings>0?'red':'') + '">' + wpData.open_findings + '</div></div>'
    + '</div>'
    + infoBox
    + methodologyHtml
    + ftePlanningHtml
    + p1p4Html
    + nexiHtml
    + riskMatrixHtml
    + accordionHtml
    + findingsHtml;
}

function applyAuditFilters() {
  const riskFilter = (document.getElementById('filter-risk')||{}).value || '';
  const yearFilter = parseInt((document.getElementById('filter-year')||{}).value || '0', 10);
  const auditTypeForRiskYear = (risk, year) => {
    if (risk === 'High') return 'Full Audit';
    if (risk === 'Medium') return year === 2 ? 'Follow-up Audit' : 'Full Audit';
    return 'DI Review';
  };
  document.querySelectorAll('[data-inherent]').forEach(row => {
    const inherent = row.getAttribute('data-inherent');
    const matchRisk = !riskFilter || inherent === riskFilter;
    row.style.display = matchRisk ? '' : 'none';
    if (yearFilter && matchRisk) {
      const sel = row.querySelector('.audit-type-sel');
      if (sel) sel.value = auditTypeForRiskYear(inherent, yearFilter);
    }
  });
}

function showDARModal(idx) {
  const reports = window._darReports || [];
  const r = reports[idx];
  if (!r) return;
  const statusHtml = r.status === 'Draft' ? '<span style="background:rgba(124,106,255,0.2);color:var(--accent);border-radius:4px;padding:2px 8px;font-size:11px">Draft</span>'
    : r.status === 'In Review' ? '<span style="background:rgba(251,191,36,0.2);color:var(--yellow);border-radius:4px;padding:2px 8px;font-size:11px">In Review</span>'
    : '<span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 8px;font-size:11px">Finalised</span>';
  document.getElementById('dar-modal-content').innerHTML = `
    <div style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:12px;gap:12px">
      <div>
        <div style="font-size:16px;font-weight:700;margin-bottom:4px">${r.title}</div>
        <div style="font-size:12px;color:var(--muted)"><code>${r.id}</code> &nbsp;·&nbsp; Responsible: ${r.auditor} &nbsp;·&nbsp; Target issue: ${r.date}</div>
      </div>
      ${statusHtml}
    </div>
    <table class="def-table" style="margin-bottom:16px">
      <tr><td class="term">Scope</td><td class="def">${r.scope}</td></tr>
      <tr><td class="term">Findings</td><td class="def">${r.findings > 0 ? '<span style="color:var(--orange)">' + r.findings + ' open finding(s)</span>' : '<span style="color:var(--green)">No open findings</span>'}</td></tr>
      <tr><td class="term">Summary</td><td class="def">${r.summary}</td></tr>
      <tr><td class="term">Next Step</td><td class="def">${r.status === 'Finalised' ? 'Report presented to board. Findings tracked in Board Report tab.' : 'Report in preparation. Working papers are the evidence base. Human review required before board distribution.'}</td></tr>
    </table>
    <p style="font-size:11px;color:var(--muted);margin:0">&#9432; This is a draft audit report. It has not been finalised or presented to the board. For working paper detail, use the Working Papers tab.</p>
  `;
  document.getElementById('dar-modal').classList.add('open');
}

function showLFAP(period, el) {
  if (el) {
    document.querySelectorAll('.lfap-tab').forEach(t => t.classList.remove('active'));
    el.classList.add('active');
  }
  const LFAP_DATA = {
    'Q1': [{id:'F-2024-003-01', title:'Exit strategy not documented', source:'IAR-2024-003', priority:'HIGH', owner:'Legal & Procurement', due:'2025-03-31', status:'Overdue', note:'Deadline extended to 2025-06-30.'},],
    'Q2': [{id:'F-2024-003-01', title:'Exit strategy not documented', source:'IAR-2024-003', priority:'HIGH', owner:'Legal & Procurement', due:'2025-06-30', status:'Overdue', note:'Second extension. Escalation to CRO recommended.'},{id:'F-2024-003-02', title:'PayLink SLAs missing (Art. 30(2)(d))', source:'IAR-2024-003', priority:'MEDIUM', owner:'Contract Manager', due:'2025-05-31', status:'On Track', note:''},],
    'Q3': [{id:'F-2024-003-02', title:'PayLink SLAs missing (Art. 30(2)(d))', source:'IAR-2024-003', priority:'MEDIUM', owner:'Contract Manager', due:'2025-05-31', status:'Closed', note:'SLA annex signed 2025-05-28. Verified by Internal Audit.'},],
    'Q4': [],
    '1H': [{id:'F-2024-003-01', title:'Exit strategy not documented', source:'IAR-2024-003', priority:'HIGH', owner:'Legal & Procurement', due:'2025-06-30', status:'Overdue', note:''},{id:'F-2024-003-02', title:'PayLink SLAs missing', source:'IAR-2024-003', priority:'MEDIUM', owner:'Contract Manager', due:'2025-05-31', status:'Closed', note:'Closed Q3.'},],
    'Full Year': [{id:'F-2024-003-01', title:'Exit strategy not documented', source:'IAR-2024-003', priority:'HIGH', owner:'Legal & Procurement', due:'2025-06-30', status:'Overdue', note:''},{id:'F-2024-003-02', title:'PayLink SLAs missing', source:'IAR-2024-003', priority:'MEDIUM', owner:'Contract Manager', due:'2025-05-31', status:'Closed', note:''},],
  };
  const rows = LFAP_DATA[period] || [];
  const target = document.getElementById('lfap-content');
  if (!target) return;
  if (!rows.length) {
    target.innerHTML = '<p style="font-size:12px;color:var(--muted);padding:8px 0">No follow-up actions recorded for ' + period + '.</p>';
    return;
  }
  const statusBadge = s => s === 'Closed' ? '<span style="background:rgba(74,222,128,0.2);color:var(--green);border-radius:4px;padding:2px 7px;font-size:11px">Closed</span>'
    : s === 'On Track' ? '<span style="background:rgba(74,222,128,0.1);color:var(--green);border-radius:4px;padding:2px 7px;font-size:11px">On Track</span>'
    : '<span style="background:rgba(251,191,36,0.2);color:var(--yellow);border-radius:4px;padding:2px 7px;font-size:11px">Overdue</span>';
  const priColor = p => ({HIGH:'var(--red)',MEDIUM:'var(--orange)',LOW:'var(--green)'}[p]||'var(--muted)');
  target.innerHTML = '<div class="table-wrap"><table>'
    + '<thead><tr><th>Finding</th><th>Source</th><th>Priority</th><th>Owner</th><th>Due</th><th>Status</th><th>Note</th></tr></thead><tbody>'
    + rows.map(r => `<tr>
        <td style="font-size:12px"><strong>${r.id}</strong><br>${r.title}</td>
        <td style="font-size:11px;color:var(--muted)">${r.source}</td>
        <td><span style="color:${priColor(r.priority)};font-size:12px;font-weight:600">${r.priority}</span></td>
        <td style="font-size:12px">${r.owner}</td>
        <td style="font-size:12px;color:var(--muted)">${r.due}</td>
        <td>${statusBadge(r.status)}</td>
        <td style="font-size:11px;color:var(--muted)">${r.note||'—'}</td>
      </tr>`).join('')
    + '</tbody></table></div>';
}

// Language toggle
const TRANSLATIONS = {
  en: {
    advisory: '⚠ Advisory — Human review required before regulatory use'
  },
  da: {
    advisory: '⚠ Rådgivende — Menneskelig gennemgang kræves inden regulatorisk brug'
  }
};
function setLang(lang) {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (TRANSLATIONS[lang] && TRANSLATIONS[lang][key]) el.textContent = TRANSLATIONS[lang][key];
  });
  const enBtn = document.getElementById('lang-en');
  const daBtn = document.getElementById('lang-da');
  if (enBtn && daBtn) {
    if (lang === 'en') {
      enBtn.style.background = 'var(--accent)'; enBtn.style.color = '#fff'; enBtn.style.border = 'none';
      daBtn.style.background = 'var(--surface2)'; daBtn.style.color = 'var(--muted)'; daBtn.style.border = '1px solid var(--border)';
    } else {
      daBtn.style.background = 'var(--accent)'; daBtn.style.color = '#fff'; daBtn.style.border = 'none';
      enBtn.style.background = 'var(--surface2)'; enBtn.style.color = 'var(--muted)'; enBtn.style.border = '1px solid var(--border)';
    }
  }
  try { localStorage.setItem('gamma7-lang', lang); } catch(e) {}
}
(function initLang() {
  try { const l = localStorage.getItem('gamma7-lang'); if (l) setLang(l); } catch(e) {}
})();

// Load data on page load
const _loadStart = Date.now();
setTimeout(() => {
  const tb = document.getElementById('overview-table');
  if (tb && tb.querySelector('td.empty') && tb.querySelector('td.empty').textContent.includes('Loading')) {
    tb.innerHTML = `<tr><td colspan="5" class="empty" style="color:var(--red)">Still loading after ${Math.round((Date.now()-_loadStart)/1000)}s — check browser console (F12) for errors. Server: <a href="/api/analyses" target="_blank">/api/analyses</a></td></tr>`;
  }
}, 5000);
(async () => {
  try {
    await loadOverview();
    await Promise.all([loadContracts(), loadRegister(), loadReconciliation(), loadWorkPapers(), loadAuditIntelligence(), loadBoardReport()]);
  } catch(e) {
    console.error('Dashboard init error:', e);
    const tb = document.getElementById('overview-table');
    if (tb) tb.innerHTML = `<tr><td colspan="5" class="empty" style="color:var(--red)">Init error: ${e.message}</td></tr>`;
  }
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
    """Return instant hardcoded demo data — no pipeline calls, no AI spend."""
    contracts_dir = pathlib.Path(__file__).parent / "synthetic_contracts"

    _DEMO_ASSESSMENTS_GAPS = [
        {"requirement_id": "ART30-1", "requirement_article": "DORA Art. 30(1)", "requirement_summary": "Written contract required", "status": "missing", "risk_level": "High", "gap_detail": "No formal SLA or written agreement found.", "effort_level": "H", "fte_days": 15, "completion_pct": 0, "target_date": "2026-03-31", "verified": False},
        {"requirement_id": "ART30-2a", "requirement_article": "DORA Art. 30(2)(a)", "requirement_summary": "Service description", "status": "partial", "risk_level": "Medium", "gap_detail": "Service description present but lacks measurable SLAs.", "effort_level": "M", "fte_days": 5, "completion_pct": 20, "target_date": "2026-03-31", "verified": False},
        {"requirement_id": "ART30-2b", "requirement_article": "DORA Art. 30(2)(b)", "requirement_summary": "Locations of data processing", "status": "missing", "risk_level": "High", "gap_detail": "Data processing locations not specified.", "effort_level": "M", "fte_days": 5, "completion_pct": 0, "target_date": "2026-04-30", "verified": False},
        {"requirement_id": "ART30-2c", "requirement_article": "DORA Art. 30(2)(c)", "requirement_summary": "Availability provisions", "status": "missing", "risk_level": "High", "gap_detail": "No uptime/availability SLA present.", "effort_level": "M", "fte_days": 5, "completion_pct": 0, "target_date": "2026-04-30", "verified": False},
        {"requirement_id": "ART30-3", "requirement_article": "DORA Art. 30(3)", "requirement_summary": "Audit rights", "status": "missing", "risk_level": "High", "gap_detail": "Contract does not grant audit rights to the financial entity.", "effort_level": "H", "fte_days": 10, "completion_pct": 0, "target_date": "2026-06-30", "verified": False},
        {"requirement_id": "ART30-4", "requirement_article": "DORA Art. 30(4)", "requirement_summary": "Sub-contracting provisions", "status": "missing", "risk_level": "Medium", "gap_detail": "Sub-contracting chain not disclosed.", "effort_level": "L", "fte_days": 3, "completion_pct": 0, "target_date": "2026-06-30", "verified": False},
        {"requirement_id": "GDPR28-3c", "requirement_article": "GDPR Art. 28(3)(c)", "requirement_summary": "Security measures", "status": "partial", "risk_level": "Medium", "gap_detail": "Generic security language; no specific technical measures.", "effort_level": "M", "fte_days": 5, "completion_pct": 10, "target_date": "2026-03-31", "verified": False},
        {"requirement_id": "GDPR28-3h", "requirement_article": "GDPR Art. 28(3)(h)", "requirement_summary": "Deletion/return of data", "status": "missing", "risk_level": "Medium", "gap_detail": "No data return or deletion obligation on termination.", "effort_level": "M", "fte_days": 4, "completion_pct": 0, "target_date": "2026-04-30", "verified": False},
    ]

    _DEMO_ASSESSMENTS_PARTIAL = [
        {"requirement_id": "ART30-1", "requirement_article": "DORA Art. 30(1)", "requirement_summary": "Written contract required", "status": "compliant", "risk_level": "Low", "gap_detail": "", "effort_level": "L", "fte_days": 0, "completion_pct": 100, "target_date": "2025-12-31", "verified": True},
        {"requirement_id": "ART30-2a", "requirement_article": "DORA Art. 30(2)(a)", "requirement_summary": "Service description", "status": "compliant", "risk_level": "Low", "gap_detail": "", "effort_level": "L", "fte_days": 0, "completion_pct": 100, "target_date": "2025-12-31", "verified": True},
        {"requirement_id": "ART30-2b", "requirement_article": "DORA Art. 30(2)(b)", "requirement_summary": "Locations of data processing", "status": "partial", "risk_level": "Medium", "gap_detail": "EU processing stated but specific countries not listed.", "effort_level": "L", "fte_days": 2, "completion_pct": 60, "target_date": "2026-02-28", "verified": False},
        {"requirement_id": "ART30-2c", "requirement_article": "DORA Art. 30(2)(c)", "requirement_summary": "Availability provisions", "status": "partial", "risk_level": "Medium", "gap_detail": "99.5% uptime stated but no degraded service compensation.", "effort_level": "M", "fte_days": 4, "completion_pct": 50, "target_date": "2026-02-28", "verified": False},
        {"requirement_id": "ART30-3", "requirement_article": "DORA Art. 30(3)", "requirement_summary": "Audit rights", "status": "missing", "risk_level": "High", "gap_detail": "Audit rights not explicitly granted.", "effort_level": "H", "fte_days": 10, "completion_pct": 25, "target_date": "2026-05-31", "verified": False},
        {"requirement_id": "GDPR28-3c", "requirement_article": "GDPR Art. 28(3)(c)", "requirement_summary": "Security measures", "status": "compliant", "risk_level": "Low", "gap_detail": "", "effort_level": "L", "fte_days": 0, "completion_pct": 100, "target_date": "2025-12-31", "verified": True},
        {"requirement_id": "GDPR28-3h", "requirement_article": "GDPR Art. 28(3)(h)", "requirement_summary": "Deletion/return of data", "status": "partial", "risk_level": "Medium", "gap_detail": "Deletion on request stated; retention schedule absent.", "effort_level": "L", "fte_days": 3, "completion_pct": 70, "target_date": "2026-02-28", "verified": False},
    ]

    _DEMO_ASSESSMENTS_COMPLIANT = [
        {"requirement_id": "ART30-1", "requirement_article": "DORA Art. 30(1)", "requirement_summary": "Written contract required", "status": "compliant", "risk_level": "Low", "gap_detail": "", "effort_level": "L", "fte_days": 0, "completion_pct": 100, "target_date": "2025-10-31", "verified": True},
        {"requirement_id": "ART30-2a", "requirement_article": "DORA Art. 30(2)(a)", "requirement_summary": "Service description", "status": "compliant", "risk_level": "Low", "gap_detail": "", "effort_level": "L", "fte_days": 0, "completion_pct": 100, "target_date": "2025-10-31", "verified": True},
        {"requirement_id": "ART30-2b", "requirement_article": "DORA Art. 30(2)(b)", "requirement_summary": "Locations of data processing", "status": "compliant", "risk_level": "Low", "gap_detail": "", "effort_level": "L", "fte_days": 0, "completion_pct": 100, "target_date": "2025-10-31", "verified": True},
        {"requirement_id": "ART30-2c", "requirement_article": "DORA Art. 30(2)(c)", "requirement_summary": "Availability provisions", "status": "compliant", "risk_level": "Low", "gap_detail": "", "effort_level": "L", "fte_days": 0, "completion_pct": 100, "target_date": "2025-10-31", "verified": True},
        {"requirement_id": "ART30-3", "requirement_article": "DORA Art. 30(3)", "requirement_summary": "Audit rights", "status": "compliant", "risk_level": "Low", "gap_detail": "", "effort_level": "L", "fte_days": 0, "completion_pct": 100, "target_date": "2025-10-31", "verified": True},
        {"requirement_id": "ART30-4", "requirement_article": "DORA Art. 30(4)", "requirement_summary": "Sub-contracting provisions", "status": "compliant", "risk_level": "Low", "gap_detail": "", "effort_level": "L", "fte_days": 0, "completion_pct": 100, "target_date": "2025-10-31", "verified": True},
        {"requirement_id": "GDPR28-3c", "requirement_article": "GDPR Art. 28(3)(c)", "requirement_summary": "Security measures", "status": "compliant", "risk_level": "Low", "gap_detail": "", "effort_level": "L", "fte_days": 0, "completion_pct": 100, "target_date": "2025-10-31", "verified": True},
        {"requirement_id": "GDPR28-3h", "requirement_article": "GDPR Art. 28(3)(h)", "requirement_summary": "Deletion/return of data", "status": "compliant", "risk_level": "Low", "gap_detail": "", "effort_level": "L", "fte_days": 0, "completion_pct": 100, "target_date": "2025-10-31", "verified": True},
    ]

    specs = [
        {
            "analysis_id": "demo-001", "path": str(contracts_dir / "contract_01_cloudhost.md"),
            "supplier": "NordCloud Hosting A/S", "classification": "critical_important",
            "status": "gaps_found",
            "summary": {"total": 12, "compliant": 4, "partial": 2, "missing": 6, "not_applicable": 0},
            "assessments": _DEMO_ASSESSMENTS_GAPS,
            "warnings": [],
        },
        {
            "analysis_id": "demo-002", "path": str(contracts_dir / "contract_02_payment.md"),
            "supplier": "PayLink Technologies Ltd", "classification": "critical_important",
            "status": "gaps_found",
            "summary": {"total": 12, "compliant": 5, "partial": 4, "missing": 3, "not_applicable": 0},
            "assessments": _DEMO_ASSESSMENTS_PARTIAL,
            "warnings": [],
        },
        {
            "analysis_id": "demo-003", "path": str(contracts_dir / "contract_03_legacy.md"),
            "supplier": "DataCenter DK ApS", "classification": "critical_important",
            "status": "gaps_found",
            "summary": {"total": 12, "compliant": 2, "partial": 3, "missing": 7, "not_applicable": 0},
            "assessments": _DEMO_ASSESSMENTS_GAPS,
            "warnings": ["Contract predates DORA — full re-negotiation recommended."],
        },
        {
            "analysis_id": "demo-004", "path": str(contracts_dir / "contract_04_saas.md"),
            "supplier": "ComplianceTools International BV", "classification": "non_critical",
            "status": "gaps_found",
            "summary": {"total": 8, "compliant": 5, "partial": 2, "missing": 1, "not_applicable": 2},
            "assessments": _DEMO_ASSESSMENTS_PARTIAL,
            "warnings": [],
        },
        {
            "analysis_id": "demo-005", "path": str(contracts_dir / "contract_05_good.md"),
            "supplier": "SecureVault Nordic A/S", "classification": "critical_important",
            "status": "compliant",
            "summary": {"total": 12, "compliant": 12, "partial": 0, "missing": 0, "not_applicable": 0},
            "assessments": _DEMO_ASSESSMENTS_COMPLIANT,
            "warnings": [],
        },
    ]

    results = [
        PipelineResult(
            analysis_id=s["analysis_id"],
            contract_path=s["path"],
            supplier_name=s["supplier"],
            classification=s["classification"],
            overall_status=s["status"],
            summary=s["summary"],
            output_paths={},
            markdown_report="",
            warnings=s["warnings"],
            assessments=s["assessments"],
        )
        for s in specs
    ]

    register_path = contracts_dir / "register_sample.yaml"
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
        # Auto-demo: if no data source was given, load synthetic contracts so the dashboard is never blank.
        if not results and not register and not args.results_dir and not args.register:
            print("No data source specified — loading demo data (5 synthetic contracts + sample register).")
            results, register = _load_demo_results()
            print(f"  Demo: {len(results)} analyses, {len(register)} register entries loaded.")
            from .audit_intelligence.audit_universe import build_universe
            from .audit_intelligence.workpaper import build_workpapers, TestResult, FindingSeverity, FindingStatus
            _auto_universe = build_universe(organisation="Demo Customer A/S", scope=["iso27001"])
            _auto_wps = build_workpapers(_auto_universe)
            _AUTO_TR = [
                TestResult.PASSED, TestResult.PASSED, TestResult.PASSED,
                TestResult.EXCEPTION, TestResult.PASSED, TestResult.EXCEPTION,
                TestResult.NOT_TESTED, TestResult.PASSED, TestResult.PASSED,
                TestResult.EXCEPTION, TestResult.NOT_APPLICABLE, TestResult.PASSED,
            ]
            for _ai, _awp in enumerate(_auto_wps.work_papers):
                _awp.test_result = _AUTO_TR[_ai % len(_AUTO_TR)]
                if _awp.test_result == TestResult.EXCEPTION and not _awp.findings:
                    _awp.add_finding(
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
            args = argparse.Namespace(**{**vars(args), "_demo_wps": _auto_wps})

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
