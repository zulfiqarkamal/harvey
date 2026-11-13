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
</style>
</head>
<body>

<header>
  <div class="logo">GAMMA7 <span>Audit &amp; Compliance</span></div>
  <div class="advisory">⚠ Advisory — Human review required before regulatory use</div>
</header>

<nav>
  <button class="active" onclick="showTab('overview')">Overview</button>
  <button onclick="showTab('contracts')">Contracts</button>
  <button onclick="showTab('register')">ICT Register</button>
  <button onclick="showTab('reconciliation')">Reconciliation</button>
  <button onclick="showTab('workpapers')">Working Papers</button>
  <button onclick="showTab('audit')">Audit Intelligence</button>
</nav>

<main>
  <!-- Overview tab -->
  <div id="tab-overview" class="tab-pane active">
    <div class="summary-cards" id="summary-cards">
      <div class="card"><div class="label">Contracts</div><div class="value" id="stat-total">—</div></div>
      <div class="card"><div class="label">Gaps Found</div><div class="value yellow" id="stat-gaps">—</div></div>
      <div class="card"><div class="label">Compliant</div><div class="value green" id="stat-compliant">—</div></div>
      <div class="card"><div class="label">Register Entries</div><div class="value" id="stat-register">—</div></div>
      <div class="card"><div class="label">Register Gaps</div><div class="value red" id="stat-recon-gaps">—</div></div>
    </div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Supplier</th><th>Classification</th><th>Status</th><th>Missing</th><th>Satisfied</th>
        </tr></thead>
        <tbody id="overview-table"><tr><td colspan="5" class="empty">Loading…</td></tr></tbody>
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
          <th>Analysis ID</th><th>Supplier</th><th>Contract</th><th>Classification</th><th>Status</th><th>Missing</th>
        </tr></thead>
        <tbody id="contracts-table"><tr><td colspan="6" class="empty">Loading…</td></tr></tbody>
      </table>
    </div>
    <div id="contract-detail" class="detail-panel"></div>
  </div>

  <!-- Register tab -->
  <div id="tab-register" class="tab-pane">
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Entry ID</th><th>Supplier</th><th>Country</th><th>Service Type</th>
          <th>Contract Ref</th><th>Classification</th><th>Exit Strategy</th>
        </tr></thead>
        <tbody id="register-table"><tr><td colspan="7" class="empty">Loading…</td></tr></tbody>
      </table>
    </div>
  </div>

  <!-- Reconciliation tab -->
  <div id="tab-reconciliation" class="tab-pane">
    <div id="recon-content"><div class="loading">Loading reconciliation…</div></div>
  </div>

  <!-- Working Papers tab (Agent 4) -->
  <div id="tab-workpapers" class="tab-pane">
    <div id="wp-content"><div class="loading">Loading working papers…</div></div>
  </div>

  <!-- Audit Intelligence tab (Agent 4) -->
  <div id="tab-audit" class="tab-pane">
    <div id="audit-content"><div class="loading">Loading audit intelligence…</div></div>
  </div>
</main>

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
    tbody.innerHTML = '<tr><td colspan="5" class="empty">No analyses loaded</td></tr>';
  } else {
    tbody.innerHTML = analyses.map(a => `<tr>
      <td>${a.supplier_name}</td>
      <td>${classBadge(a.classification)}</td>
      <td>${statusBadge(a.overall_status)}</td>
      <td>${a.summary ? a.summary.missing || 0 : 0}</td>
      <td>${a.summary ? a.summary.satisfied || 0 : 0}</td>
    </tr>`).join('');
  }
}

async function loadContracts() {
  const tbody = document.getElementById('contracts-table');
  if (!analyses.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="empty">No analyses loaded</td></tr>';
    return;
  }
  tbody.innerHTML = analyses.map(a => `<tr onclick="showContractDetail('${a.contract_key || a.analysis_id}')">
    <td><code>${a.analysis_id}</code></td>
    <td>${a.supplier_name}</td>
    <td style="color:var(--muted);font-size:12px">${(a.contract_path.replace(/\\\\/g,'/').split('/').pop())}</td>
    <td>${classBadge(a.classification)}</td>
    <td>${statusBadge(a.overall_status)}</td>
    <td>${a.summary ? a.summary.missing || 0 : 0}</td>
  </tr>`).join('');
}

async function showContractDetail(id) {
  const panel = document.getElementById('contract-detail');
  const res = await fetch('/api/analyses/' + id);
  const data = await res.json();
  panel.className = 'detail-panel open';
  panel.innerHTML = `
    <h3>${data.supplier_name} — ${data.analysis_id}</h3>
    ${data.warnings && data.warnings.length ? `<p style="color:var(--orange);margin-bottom:8px">⚠ ${data.warnings.join(' | ')}</p>` : ''}
    <pre>${data.markdown_report || '(no report)'}</pre>
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
    <td style="color:var(--muted);font-size:12px"><code>${e.contract_reference}</code></td>
    <td>${classBadge(e.classification)}</td>
    <td>${e.exit_strategy_documented
      ? '<span style="color:var(--green)">✓</span>'
      : '<span style="color:var(--red)">✗</span>'}</td>
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
      <div class="card"><div class="label">Total register</div><div class="value">${s.total_register_entries || 0}</div></div>
      <div class="card"><div class="label">Analyses</div><div class="value">${s.total_analyses || 0}</div></div>
      <div class="card"><div class="label">Matched</div><div class="value green">${s.matched || 0}</div></div>
      <div class="card"><div class="label">Register only</div><div class="value red">${s.register_only || 0}</div></div>
      <div class="card"><div class="label">Contract only</div><div class="value yellow">${s.analysis_only || 0}</div></div>
    </div>

    ${data.matched && data.matched.length ? `
    <div class="recon-section">
      <div class="section-title">Matched (register ↔ contract)</div>
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
      <div class="section-title" style="color:var(--red)">⚠ Register entries without contract assessment</div>
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
      <div class="section-title" style="color:var(--yellow)">⚠ Contracts not in register</div>
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
      <a class="btn" href="/api/workpapers/report" target="_blank">⬇ Download Audit Report</a>
    </div>
    <div class="summary-cards" style="margin-bottom:20px">
      <div class="card"><div class="label">Controls in scope</div><div class="value">${data.total_controls}</div></div>
      <div class="card"><div class="label">Tested</div><div class="value">${data.tested_count}</div></div>
      <div class="card"><div class="label">Passed</div><div class="value green">${data.passed_count}</div></div>
      <div class="card"><div class="label">Exceptions</div><div class="value ${data.exception_count>0?'yellow':''}">${data.exception_count}</div></div>
      <div class="card"><div class="label">Open findings</div><div class="value ${data.open_findings>0?'red':''}">${data.open_findings}</div></div>
    </div>
    <div id="wp-heatmap" style="margin-bottom:20px"></div>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>Control ID</th><th>Title</th><th>Domain</th><th>Inherent Risk</th><th>Result</th><th>Findings</th>
        </tr></thead>
        <tbody>${data.work_papers.map(wp => `<tr onclick="showWPDetail('${wp.control_id}')" style="cursor:pointer">
          <td><code>${wp.control_id}</code></td>
          <td>${wp.control_title}</td>
          <td style="color:var(--muted);font-size:12px">${wp.control_domain}</td>
          <td class="${riskClass(wp.inherent_risk)}">${wp.inherent_risk}</td>
          <td class="${resultClass(wp.test_result)}">${resultIcon(wp.test_result)} ${wp.test_result.replace(/_/g,' ')}</td>
          <td>${wp.open_finding_count>0?`<span style="color:var(--orange)">${wp.finding_count} (${wp.open_finding_count} open)</span>`:wp.finding_count>0?wp.finding_count:''}</td>
        </tr>`).join('')}</tbody>
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
  panel.innerHTML = '<div class="loading" style="padding:20px">Loading…</div>';
  const res = await fetch('/api/workpapers/' + encodeURIComponent(controlId));
  if (!res.ok) { panel.innerHTML = '<div class="empty">Not found.</div>'; return; }
  const wp = await res.json();
  const severityColor = s => ({CRITICAL:'var(--red)',HIGH:'var(--orange)',MEDIUM:'var(--yellow)',LOW:'var(--green)',INFO:'var(--muted)'}[s]||'var(--muted)');
  const statusColor = s => s==='open'?'var(--orange)':s==='closed'?'var(--green)':'var(--muted)';
  panel.innerHTML = `
    <h3><code>${wp.control_id}</code> — ${wp.control_title}</h3>
    <p style="color:var(--muted);font-size:12px;margin:6px 0 12px">${wp.control_domain} · Risk: <strong>${wp.inherent_risk}</strong> · Result: <strong>${wp.test_result.replace(/_/g,' ')}</strong></p>
    ${wp.audit_objective ? `<p style="font-size:13px;margin-bottom:12px;color:var(--muted)"><strong style="color:var(--text)">Audit objective:</strong> ${wp.audit_objective}</p>` : ''}
    ${wp.expected_evidence_artifacts && wp.expected_evidence_artifacts.length ? `
      <p style="font-size:12px;margin-bottom:12px"><strong>Expected evidence:</strong>
      <span style="color:var(--muted)">${wp.expected_evidence_artifacts.join(', ')}</span></p>` : ''}
    ${wp.testing_notes ? `<p style="font-size:12px;margin-bottom:12px"><strong>Testing notes:</strong> <span style="color:var(--muted)">${wp.testing_notes}</span></p>` : ''}
    ${wp.findings.length ? `
      <div style="margin-top:16px">
        <div class="section-title">Findings (${wp.findings.length})</div>
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
    + '<div class="table-wrap"><table><thead><tr><th>Control ID</th><th>Title</th><th>Domain</th><th>Risk</th><th>Result</th><th>Open Findings</th></tr></thead><tbody>'
    + wpData.work_papers.map(wp => '<tr onclick="showWPDetail(\'' + wp.control_id + '\')" style="cursor:pointer">'
      + '<td><code>' + wp.control_id + '</code></td>'
      + '<td>' + wp.control_title + '</td>'
      + '<td style="color:var(--muted);font-size:12px">' + wp.control_domain + '</td>'
      + '<td class="' + riskClass(wp.inherent_risk) + '">' + wp.inherent_risk + '</td>'
      + '<td>' + resultIcon(wp.test_result) + ' ' + wp.test_result.replace(/_/g,' ') + '</td>'
      + '<td>' + (wp.open_finding_count>0?'<span style="color:var(--orange)">' + wp.open_finding_count + '</span>':'—') + '</td>'
      + '</tr>').join('')
    + '</tbody></table></div>'
    + (openFindings.length ? '<div style="margin-top:20px"><div class="section-title" style="margin-bottom:12px">Open Findings Summary</div>'
      + openFindings.map(wp => '<div class="finding-row"><h4><code>' + wp.control_id + '</code> — ' + wp.control_title + '</h4><div class="finding-meta"><span class="badge" style="background:var(--surface2);color:var(--orange)">' + wp.open_finding_count + ' open finding' + (wp.open_finding_count>1?'s':'') + '</span><span class="badge badge-' + (wp.inherent_risk==='CRITICAL'?'critical':'gaps') + '">' + wp.inherent_risk + '</span></div></div>').join('')
      + '</div>' : '');
}

// Load data on page load
(async () => {
  await loadOverview();
  await Promise.all([loadContracts(), loadRegister(), loadReconciliation(), loadWorkPapers(), loadAuditIntelligence()]);
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
    actual_port = int(os.environ.get("PORT", args.port))
    print(f"\nDashboard: http://{args.host}:{actual_port}/\n")
    uvicorn.run(app, host=args.host, port=actual_port, log_level="warning")


if __name__ == "__main__":
    main()
