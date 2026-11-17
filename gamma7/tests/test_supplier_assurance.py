"""
Tests for gamma7.supplier_assurance.pipeline
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from gamma7.supplier_assurance import SupplierAssurancePipeline
from gamma7.supplier_assurance.pipeline import PipelineResult
from gamma7.engine.analyser import ComplianceStatus


SYNTHETIC_DIR = pathlib.Path(__file__).parent.parent / "synthetic_contracts"
GOOD_CONTRACT = SYNTHETIC_DIR / "contract_05_good.md"
LEGACY_CONTRACT = SYNTHETIC_DIR / "contract_03_legacy.md"


def test_pipeline_returns_pipeline_result(tmp_path):
    p = SupplierAssurancePipeline()
    result = p.run(
        contract_path=GOOD_CONTRACT,
        supplier_name="TestSupplier A/S",
        output_dir=tmp_path,
    )
    assert isinstance(result, PipelineResult)


def test_pipeline_result_has_required_fields(tmp_path):
    p = SupplierAssurancePipeline()
    result = p.run(contract_path=GOOD_CONTRACT, output_dir=tmp_path)
    assert result.analysis_id
    assert result.contract_path
    assert result.overall_status
    assert isinstance(result.summary, dict)
    assert isinstance(result.output_paths, dict)
    assert result.markdown_report


def test_pipeline_saves_json_and_markdown(tmp_path):
    p = SupplierAssurancePipeline()
    result = p.run(contract_path=GOOD_CONTRACT, output_dir=tmp_path)
    assert "json" in result.output_paths
    assert "markdown" in result.output_paths
    assert pathlib.Path(result.output_paths["json"]).exists()
    assert pathlib.Path(result.output_paths["markdown"]).exists()


def test_pipeline_no_output_dir_skips_files():
    p = SupplierAssurancePipeline()
    result = p.run(contract_path=GOOD_CONTRACT)
    assert result.output_paths == {}
    assert result.markdown_report  # markdown still generated in-memory


def test_pipeline_markdown_contains_supplier_name(tmp_path):
    p = SupplierAssurancePipeline()
    result = p.run(
        contract_path=GOOD_CONTRACT,
        supplier_name="Acme Bank Provider",
        output_dir=tmp_path,
    )
    assert "Acme Bank Provider" in result.markdown_report


def test_pipeline_overall_status_is_valid_compliance_value(tmp_path):
    p = SupplierAssurancePipeline()
    result = p.run(contract_path=GOOD_CONTRACT, output_dir=tmp_path)
    valid_values = {s.value for s in ComplianceStatus}
    assert result.overall_status in valid_values


def test_pipeline_summary_has_counts(tmp_path):
    p = SupplierAssurancePipeline()
    result = p.run(contract_path=GOOD_CONTRACT, output_dir=tmp_path)
    # Summary should contain some counts — at least total_requirements or missing
    assert any(isinstance(v, int) for v in result.summary.values())


def test_legacy_contract_reports_gaps(tmp_path):
    """A known-weak contract should produce MISSING findings."""
    p = SupplierAssurancePipeline()
    result = p.run(
        contract_path=LEGACY_CONTRACT,
        classification="critical_important",
        output_dir=tmp_path,
    )
    # Legacy contract is intentionally incomplete — expect gaps
    missing = result.summary.get("missing", 0)
    assert missing > 0, f"Expected MISSING gaps in legacy contract, got summary={result.summary}"


def test_critical_important_runs_more_requirements(tmp_path):
    """Critical classification triggers scope-limited requirements."""
    p = SupplierAssurancePipeline()
    res_critical = p.run(
        contract_path=GOOD_CONTRACT,
        classification="critical_important",
        output_dir=tmp_path / "critical",
    )
    res_non_critical = p.run(
        contract_path=GOOD_CONTRACT,
        classification="non_critical",
        output_dir=tmp_path / "non_critical",
    )
    critical_total = res_critical.summary.get("total_requirements", 0)
    non_critical_total = res_non_critical.summary.get("total_requirements", 0)
    # Critical should have >= same number of assessed requirements
    assert critical_total >= non_critical_total


def test_pipeline_no_warnings_on_good_contract():
    p = SupplierAssurancePipeline()
    result = p.run(contract_path=GOOD_CONTRACT)
    assert result.warnings == []


def test_batch_returns_one_result_per_contract(tmp_path):
    p = SupplierAssurancePipeline()
    contracts = [
        {"path": str(GOOD_CONTRACT), "supplier_name": "SupplierA"},
        {"path": str(LEGACY_CONTRACT), "supplier_name": "SupplierB"},
    ]
    results = p.run_batch(contracts, output_dir=tmp_path)
    assert len(results) == 2
    assert all(isinstance(r, PipelineResult) for r in results)


def test_batch_preserves_supplier_names(tmp_path):
    p = SupplierAssurancePipeline()
    contracts = [
        {"path": str(GOOD_CONTRACT), "supplier_name": "Alpha A/S"},
        {"path": str(LEGACY_CONTRACT), "supplier_name": "Beta ApS"},
    ]
    results = p.run_batch(contracts, output_dir=tmp_path)
    assert results[0].supplier_name == "Alpha A/S"
    assert results[1].supplier_name == "Beta ApS"


def test_pipeline_missing_file_raises():
    import pytest
    p = SupplierAssurancePipeline()
    with pytest.raises(FileNotFoundError):
        p.run(contract_path="/nonexistent/contract.md")


def test_markdown_report_has_disclaimer():
    p = SupplierAssurancePipeline()
    result = p.run(contract_path=GOOD_CONTRACT)
    # Report must carry the human review caveat
    assert "human" in result.markdown_report.lower() or "review" in result.markdown_report.lower()
