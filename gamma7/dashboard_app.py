"""
Gamma7 Supplier Assurance — Web Dashboard

FastAPI app serving a minimal HTML dashboard showing:
  - Analysis results for assessed contracts
  - ICT register reconciliation status
  - Per-requirement compliance breakdown

Usage:
    python -m gamma7.dashboard_app --results-dir output/ [--register register.yaml]
    python -m gamma7.dashboard_app --demo   # loads synthetic contracts

IMPORTANT: This dashboard is for internal review only.
All findings require human review before regulatory reporting.
"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from .engine.analyser import ContractAnalyser, ContractMetadata
from .engine.document_loader import load as load_document
from .engine.register_reconciler import (
    ReconciliationReport,
    RegisterEntry,
    load_register,
    reconcile,
)
from .supplier_assurance.pipeline import PipelineResult, SupplierAssurancePipeline


# ---------------------------------------------------------------------------
# App state
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Gamma7 Supplier Assurance Dashboard",
    description="DORA Art. 30 / GDPR Art. 28 contract analysis — advisory only",
    version="0.1.0",
)

_pipeline_results: list[PipelineResult] = []
_register: list[RegisterEntry] = []
_reconciliation: Optional[ReconciliationReport] = None


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
            "contract_path": r.contract_path,
            "supplier_name": r.supplier_name,
            "classification": r.classification,
            "overall_status": r.overall_status,
            "summary": r.summary,
            "warnings": r.warnings,
        }
        for r in _pipeline_results
    ]


@app.get("/api/analyses/{analysis_id}")
def get_analysis(analysis_id: str):
    """Return full analysis detail including per-requirement assessments."""
    for r in _pipeline_results:
        if r.analysis_id == analysis_id:
            return {
                "analysis_id": r.analysis_id,
                "contract_path": r.contract_path,
                "supplier_name": r.supplier_name,
                "classification": r.classification,
                "overall_status": r.overall_status,
                "summary": r.summary,
                "markdown_report": r.markdown_report,
                "warnings": r.warnings,
                "overlay_summary": r.overlay_summary,
            }
    raise HTTPException(status_code=404, detail=f"Analysis '{analysis_id}' not found")


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
</style>
</head>
<body>

<header>
  <div class="logo">GAMMA7 <span>Supplier Assurance</span></div>
  <div class="advisory">⚠ Advisory — Human review required before regulatory use</div>
</header>

<nav>
  <button class="active" onclick="showTab('overview')">Overview</button>
  <button onclick="showTab('contracts')">Contracts</button>
  <button onclick="showTab('register')">ICT Register</button>
  <button onclick="showTab('reconciliation')">Reconciliation</button>
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
  tbody.innerHTML = analyses.map(a => `<tr onclick="showContractDetail('${a.analysis_id}')">
    <td><code>${a.analysis_id}</code></td>
    <td>${a.supplier_name}</td>
    <td style="color:var(--muted);font-size:12px">${a.contract_path.split('/').pop()}</td>
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

// Load data on page load
(async () => {
  await loadOverview();
  await Promise.all([loadContracts(), loadRegister(), loadReconciliation()]);
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
) -> FastAPI:
    """Create and configure the app with pre-loaded data."""
    global _pipeline_results, _register, _reconciliation

    _pipeline_results = results
    _register = register
    _reconciliation = None

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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.demo:
        print("Loading demo data (5 synthetic contracts + sample register)…")
        results, register = _load_demo_results()
        print(f"  Loaded {len(results)} analyses, {len(register)} register entries")
    else:
        results = []
        if args.results_dir:
            results = _load_results_from_dir(args.results_dir)
            print(f"Loaded {len(results)} analyses from {args.results_dir}")
        register = []
        if args.register:
            register = load_register(args.register)
            print(f"Loaded {len(register)} register entries from {args.register}")

    create_app(results, register, customer_name=args.customer)
    print(f"\nDashboard: http://{args.host}:{args.port}/\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
