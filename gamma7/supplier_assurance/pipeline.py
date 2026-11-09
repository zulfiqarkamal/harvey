"""
Gamma7 Supplier Assurance Pipeline
====================================
Orchestrates the full Agent 2 workflow:
  1. Load contract document (Markdown, text, or PDF)
  2. Run heuristic gap analysis (DORA Art. 30 + GDPR Art. 28)
  3. Generate structured report (JSON + Markdown)
  4. Return result for human review

No LLM calls — pure deterministic heuristic analysis.
Human review is required before any compliance determination.
"""

from __future__ import annotations

import datetime
import pathlib
from dataclasses import dataclass
from typing import Optional

from ..engine.analyser import ContractAnalyser, ContractMetadata
from ..engine.document_loader import load as load_document, LoadedDocument
from ..engine.report_generator import save as save_report, generate_markdown


@dataclass
class PipelineResult:
    analysis_id: str
    contract_path: str
    supplier_name: str
    classification: str
    overall_status: str
    summary: dict
    output_paths: dict
    markdown_report: str
    warnings: list[str]


class SupplierAssurancePipeline:
    """
    End-to-end pipeline for DORA Art. 30 / GDPR Art. 28 contract assessment.

    Usage:
        pipeline = SupplierAssurancePipeline()
        result = pipeline.run(
            contract_path="path/to/contract.md",
            supplier_name="Acme A/S",
            customer_name="Nordbank",
            classification="critical_important",
            output_dir="output/",
        )
        print(result.markdown_report)
    """

    def __init__(self, frameworks: Optional[list[str]] = None):
        self._frameworks = frameworks or ["dora_art30", "gdpr_art28"]
        self._analyser = ContractAnalyser(self._frameworks)

    def run(
        self,
        contract_path: str | pathlib.Path,
        supplier_name: str = "Unknown Supplier",
        customer_name: str = "Unknown Customer",
        classification: str = "non_critical",
        output_dir: Optional[str | pathlib.Path] = None,
        contract_id: Optional[str] = None,
    ) -> PipelineResult:
        """
        Run the full analysis pipeline on one contract.

        Args:
            contract_path: Path to the contract file (Markdown, text, or PDF).
            supplier_name: Name of the ICT third-party service provider.
            customer_name: Name of the financial entity (the regulated party).
            classification: "critical_important" or "non_critical" per DORA Art. 30(1).
            output_dir: Directory to save JSON + Markdown output. Skip if None.
            contract_id: Optional contract reference identifier.

        Returns:
            PipelineResult with analysis summary and report content.
        """
        path = pathlib.Path(contract_path)
        warnings: list[str] = []

        doc: LoadedDocument = load_document(path)
        if not doc.text.strip():
            warnings.append(f"Document appears empty: {path}")

        c_id = contract_id or f"SA-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        metadata = ContractMetadata(
            contract_id=c_id,
            supplier_name=supplier_name,
            customer_name=customer_name,
            classification=classification,
            file_name=path.name,
        )

        analysis = self._analyser.analyse(doc.text, classification=classification, contract_metadata=metadata)

        md_report = generate_markdown(analysis)
        output_paths: dict = {}
        if output_dir:
            output_paths = {k: str(v) for k, v in save_report(analysis, output_dir, stem=c_id).items()}

        return PipelineResult(
            analysis_id=analysis.analysis_id,
            contract_path=str(path),
            supplier_name=supplier_name,
            classification=classification,
            overall_status=analysis.overall_status.value,
            summary=analysis.summary_stats,
            output_paths=output_paths,
            markdown_report=md_report,
            warnings=warnings,
        )

    def run_batch(
        self,
        contracts: list[dict],
        output_dir: str | pathlib.Path,
    ) -> list[PipelineResult]:
        """
        Analyse multiple contracts in sequence.

        Each entry in `contracts` is a dict with keys:
            path (required), supplier_name, customer_name, classification, contract_id
        """
        results = []
        for spec in contracts:
            r = self.run(
                contract_path=spec["path"],
                supplier_name=spec.get("supplier_name", "Unknown"),
                customer_name=spec.get("customer_name", "Unknown"),
                classification=spec.get("classification", "non_critical"),
                output_dir=output_dir,
                contract_id=spec.get("contract_id"),
            )
            results.append(r)
        return results
