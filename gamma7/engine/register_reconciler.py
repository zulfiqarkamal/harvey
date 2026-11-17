"""
Gamma7 ICT Register Reconciler

Reconciles contract analysis results against the DORA ICT Register of Information.
Per DORA Article 28, financial entities must maintain a register of all ICT
third-party service providers and their contracts.

This module:
1. Loads a register of ICT providers (YAML or CSV)
2. Matches each register entry to contract analysis results by contract reference
   or normalised supplier name
3. Identifies gaps:
   - Suppliers in the register with no contract assessed → need assessment
   - Contracts analysed for suppliers not in the register → register incomplete
4. Produces a ReconciliationReport for human review

IMPORTANT: Reconciliation findings require human review before regulatory reporting.
"""

from __future__ import annotations

import csv
import datetime
import io
import json
import pathlib
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RegisterEntry:
    """One row in the DORA ICT Register of Information."""
    entry_id: str
    supplier_name: str
    supplier_country: str                    # ISO 3166-1 alpha-2
    service_type: str                        # e.g. cloud_hosting, payment_processing
    service_description: str
    contract_reference: str                  # links to ContractMetadata.contract_id
    classification: str                      # critical_important | non_critical
    functions_supported: list[str] = field(default_factory=list)
    contract_start_date: Optional[str] = None
    contract_end_date: Optional[str] = None
    supplier_lei: Optional[str] = None       # Legal Entity Identifier (optional)
    sub_contractors: list[str] = field(default_factory=list)
    exit_strategy_documented: bool = False
    notes: str = ""


@dataclass
class MatchedEntry:
    """A register entry successfully matched to a contract analysis."""
    register_entry: RegisterEntry
    analysis_id: str
    contract_path: str
    supplier_name_in_contract: str
    overall_status: str               # from ContractAnalysisResult.overall_status
    missing_count: int
    satisfied_count: int
    match_method: str                 # "contract_reference" | "supplier_name"


@dataclass
class ReconciliationReport:
    """
    Full reconciliation between the ICT register and contract analysis results.

    Human review is required before regulatory reporting.
    """
    report_id: str
    generated_at: str
    customer_name: str
    matched: list[MatchedEntry]
    register_only: list[RegisterEntry]       # in register, no contract assessed
    analysis_only: list[dict]                # contracts analysed, not in register
    summary: dict

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)

    def to_markdown(self) -> str:
        lines = [
            f"# ICT Register Reconciliation Report",
            f"",
            f"**Customer:** {self.customer_name}  ",
            f"**Generated:** {self.generated_at}  ",
            f"**Report ID:** {self.report_id}",
            f"",
            f"> ⚠️ This report is advisory. Human review is required before regulatory use.",
            f"",
            f"## Summary",
            f"",
            f"| Category | Count |",
            f"|---|---|",
            f"| Register entries | {self.summary['total_register_entries']} |",
            f"| Contracts assessed | {self.summary['total_analyses']} |",
            f"| Matched (register ↔ contract) | {self.summary['matched']} |",
            f"| **Register only** (not yet assessed) | **{self.summary['register_only']}** |",
            f"| **Contract only** (not in register) | **{self.summary['analysis_only']}** |",
            f"",
        ]

        if self.matched:
            lines += [
                "## Matched Contracts",
                "",
                "| Supplier | Contract Ref | Classification | Status | Missing |",
                "|---|---|---|---|---|",
            ]
            for m in self.matched:
                lines.append(
                    f"| {m.register_entry.supplier_name} "
                    f"| {m.register_entry.contract_reference} "
                    f"| {m.register_entry.classification} "
                    f"| {m.overall_status} "
                    f"| {m.missing_count} |"
                )
            lines.append("")

        if self.register_only:
            lines += [
                "## Register Entries Without Contract Assessment",
                "",
                "These suppliers are in the DORA register but have no contract analysis. "
                "An assessment is needed.",
                "",
                "| Supplier | Contract Ref | Classification | Service |",
                "|---|---|---|---|",
            ]
            for e in self.register_only:
                lines.append(
                    f"| {e.supplier_name} "
                    f"| {e.contract_reference} "
                    f"| {e.classification} "
                    f"| {e.service_type} |"
                )
            lines.append("")

        if self.analysis_only:
            lines += [
                "## Contracts Not in Register",
                "",
                "These contracts have been analysed but have no matching register entry. "
                "The register may be incomplete.",
                "",
                "| Contract Path | Supplier (in contract) | Analysis ID |",
                "|---|---|---|",
            ]
            for a in self.analysis_only:
                lines.append(
                    f"| {a.get('contract_path', '')} "
                    f"| {a.get('supplier_name', '')} "
                    f"| {a.get('analysis_id', '')} |"
                )
            lines.append("")

        lines += [
            "---",
            "_This report was generated by Gamma7 Supplier Assurance. "
            "All findings require human review._",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Register loader
# ---------------------------------------------------------------------------

def _normalise(name: str) -> str:
    """Lowercase, strip punctuation and common legal suffixes for fuzzy matching."""
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = name.lower()
    # Remove legal entity suffixes
    for suffix in [" a/s", " aps", " as", " apbs", " ltd", " limited",
                   " bv", " gmbh", " llc", " inc", " corp", " ab", " oy"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def load_register(source: str | pathlib.Path) -> list[RegisterEntry]:
    """
    Load the ICT register from a YAML or CSV file.

    YAML format: a dict with top-level key `entries` → list of entry dicts.
    CSV format: one row per entry, column names matching RegisterEntry fields.
    """
    path = pathlib.Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Register file not found: {path}")

    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries_raw = raw.get("entries", raw) if isinstance(raw, dict) else raw
    elif suffix == ".csv":
        reader = csv.DictReader(io.StringIO(path.read_text(encoding="utf-8")))
        entries_raw = list(reader)
    else:
        raise ValueError(f"Unsupported register format: {suffix}. Use .yaml or .csv")

    entries = []
    for row in entries_raw:
        # Coerce list fields that may come as comma-separated strings from CSV
        for list_field in ("functions_supported", "sub_contractors"):
            val = row.get(list_field, [])
            if isinstance(val, str):
                row[list_field] = [v.strip() for v in val.split(",") if v.strip()]
        # Coerce bool
        for bool_field in ("exit_strategy_documented",):
            val = row.get(bool_field, False)
            if isinstance(val, str):
                row[bool_field] = val.lower() in ("true", "yes", "1")
        # Drop any keys not in RegisterEntry
        valid_keys = {f.name for f in RegisterEntry.__dataclass_fields__.values()
                      if hasattr(f, "name")} | set(RegisterEntry.__dataclass_fields__.keys())
        filtered = {k: v for k, v in row.items() if k in valid_keys}
        entries.append(RegisterEntry(**filtered))

    return entries


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------

def reconcile(
    register: list[RegisterEntry],
    analyses: list[dict],
    customer_name: str = "Unknown Customer",
) -> ReconciliationReport:
    """
    Reconcile a register against a list of analysis result dicts.

    Each analysis dict must have at minimum:
      analysis_id, contract_path, supplier_name, overall_status, summary

    Returns a ReconciliationReport for human review.
    """
    report_id = f"RR-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')}"
    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # Build lookup maps
    # 1. By contract_reference
    analysis_by_ref: dict[str, dict] = {}
    for a in analyses:
        ref = (a.get("contract_id") or "").strip()
        if ref:
            analysis_by_ref[ref] = a

    # 2. By normalised supplier name
    analysis_by_name: dict[str, dict] = {}
    for a in analyses:
        norm = _normalise(a.get("supplier_name", ""))
        if norm:
            analysis_by_name[norm] = a

    matched: list[MatchedEntry] = []
    matched_analysis_ids: set[str] = set()

    register_only: list[RegisterEntry] = []

    for entry in register:
        # Try contract reference first (exact match)
        analysis = analysis_by_ref.get(entry.contract_reference.strip())
        method = "contract_reference"

        if analysis is None:
            # Fall back to normalised supplier name
            norm_entry = _normalise(entry.supplier_name)
            analysis = analysis_by_name.get(norm_entry)
            method = "supplier_name"

        if analysis is not None:
            summary = analysis.get("summary", {})
            matched.append(MatchedEntry(
                register_entry=entry,
                analysis_id=analysis.get("analysis_id", ""),
                contract_path=analysis.get("contract_path", ""),
                supplier_name_in_contract=analysis.get("supplier_name", ""),
                overall_status=analysis.get("overall_status", ""),
                missing_count=summary.get("missing", 0),
                satisfied_count=summary.get("satisfied", 0),
                match_method=method,
            ))
            matched_analysis_ids.add(analysis.get("analysis_id", ""))
        else:
            register_only.append(entry)

    # Contracts not matched to any register entry
    analysis_only = [
        a for a in analyses
        if a.get("analysis_id", "") not in matched_analysis_ids
    ]

    summary = {
        "total_register_entries": len(register),
        "total_analyses": len(analyses),
        "matched": len(matched),
        "register_only": len(register_only),
        "analysis_only": len(analysis_only),
        "critical_important_matched": sum(
            1 for m in matched
            if m.register_entry.classification == "critical_important"
        ),
        "critical_important_with_gaps": sum(
            1 for m in matched
            if m.register_entry.classification == "critical_important"
            and m.missing_count > 0
        ),
    }

    return ReconciliationReport(
        report_id=report_id,
        generated_at=generated_at,
        customer_name=customer_name,
        matched=matched,
        register_only=register_only,
        analysis_only=analysis_only,
        summary=summary,
    )


def reconcile_from_pipeline_results(
    register: list[RegisterEntry],
    pipeline_results: list,
    customer_name: str = "Unknown Customer",
) -> ReconciliationReport:
    """
    Convenience wrapper: accepts PipelineResult objects from the supplier assurance pipeline.
    """
    analyses = [
        {
            "analysis_id": r.analysis_id,
            "contract_path": r.contract_path,
            "supplier_name": r.supplier_name,
            "overall_status": r.overall_status,
            "summary": r.summary,
            "contract_id": r.analysis_id,  # pipeline uses analysis_id; register uses contract_reference
        }
        for r in pipeline_results
    ]
    return reconcile(register, analyses, customer_name=customer_name)


def save_report(
    report: ReconciliationReport,
    output_dir: str | pathlib.Path,
    stem: Optional[str] = None,
) -> dict[str, pathlib.Path]:
    """Save reconciliation report as JSON + Markdown. Returns paths dict."""
    out = pathlib.Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = stem or report.report_id

    json_path = out / f"{stem}.json"
    md_path = out / f"{stem}.md"

    json_path.write_text(report.to_json(), encoding="utf-8")
    md_path.write_text(report.to_markdown(), encoding="utf-8")

    return {"json": json_path, "markdown": md_path}
