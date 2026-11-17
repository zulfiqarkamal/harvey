"""
Tests for gamma7.engine.register_reconciler

Covers:
- RegisterEntry dataclass construction
- load_register (YAML + CSV)
- _normalise helper
- reconcile (match by contract ref, by name, unmatched on both sides)
- reconcile_from_pipeline_results
- save_report (JSON + Markdown)
- ReconciliationReport.to_json / to_markdown
"""

import csv
import io
import json
import pathlib
import sys

import pytest
import yaml

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from gamma7.engine.register_reconciler import (
    MatchedEntry,
    ReconciliationReport,
    RegisterEntry,
    _normalise,
    load_register,
    reconcile,
    reconcile_from_pipeline_results,
    save_report,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _entry(
    entry_id="REG-001",
    supplier_name="Acme A/S",
    contract_reference="CONTRACT-001",
    classification="critical_important",
    service_type="cloud_hosting",
    supplier_country="DK",
    service_description="Hosting services",
) -> RegisterEntry:
    return RegisterEntry(
        entry_id=entry_id,
        supplier_name=supplier_name,
        supplier_country=supplier_country,
        service_type=service_type,
        service_description=service_description,
        contract_reference=contract_reference,
        classification=classification,
    )


def _analysis(
    analysis_id="SA-001",
    contract_path="contract_01.md",
    supplier_name="Acme A/S",
    overall_status="gaps_found",
    missing=2,
    satisfied=5,
    contract_id="CONTRACT-001",
) -> dict:
    return {
        "analysis_id": analysis_id,
        "contract_path": contract_path,
        "supplier_name": supplier_name,
        "overall_status": overall_status,
        "summary": {"missing": missing, "satisfied": satisfied},
        "contract_id": contract_id,
    }


# ---------------------------------------------------------------------------
# _normalise
# ---------------------------------------------------------------------------

def test_normalise_lowercases():
    assert _normalise("ACME") == "acme"


def test_normalise_strips_as_suffix():
    assert _normalise("Nordbank A/S") == "nordbank"


def test_normalise_strips_ltd():
    assert _normalise("PayLink Technologies Ltd") == "paylink technologies"


def test_normalise_strips_bv():
    assert _normalise("ComplianceTools International BV") == "compliancetools international"


def test_normalise_strips_gmbh():
    assert _normalise("FooBar GmbH") == "foobar"


def test_normalise_strips_punctuation():
    # Period in "Inc." is stripped by punctuation removal, not the suffix rule,
    # so "inc" remains — both "Foo & Bar, Inc." and "Foo & Bar Inc" → "foo bar inc"
    assert _normalise("Foo & Bar, Inc.") == "foo bar inc"


def test_normalise_collapses_whitespace():
    assert "  " not in _normalise("  Acme   A/S  ")


def test_normalise_handles_unicode():
    result = _normalise("Å-Teknik A/S")
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# load_register — YAML
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path, data) -> pathlib.Path:
    path = tmp_path / "register.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


def test_load_register_yaml_entries_key(tmp_path):
    data = {"entries": [
        {
            "entry_id": "REG-001",
            "supplier_name": "Acme A/S",
            "supplier_country": "DK",
            "service_type": "cloud_hosting",
            "service_description": "Cloud",
            "contract_reference": "C-001",
            "classification": "critical_important",
        }
    ]}
    path = _write_yaml(tmp_path, data)
    entries = load_register(path)
    assert len(entries) == 1
    assert entries[0].entry_id == "REG-001"


def test_load_register_yaml_list_format(tmp_path):
    data = [
        {
            "entry_id": "REG-001",
            "supplier_name": "Acme A/S",
            "supplier_country": "DK",
            "service_type": "cloud_hosting",
            "service_description": "Cloud",
            "contract_reference": "C-001",
            "classification": "critical_important",
        }
    ]
    path = _write_yaml(tmp_path, data)
    entries = load_register(path)
    assert len(entries) == 1


def test_load_register_yaml_bool_coercion(tmp_path):
    data = {"entries": [
        {
            "entry_id": "REG-001",
            "supplier_name": "Acme A/S",
            "supplier_country": "DK",
            "service_type": "cloud_hosting",
            "service_description": "Cloud",
            "contract_reference": "C-001",
            "classification": "critical_important",
            "exit_strategy_documented": True,
        }
    ]}
    path = _write_yaml(tmp_path, data)
    entries = load_register(path)
    assert entries[0].exit_strategy_documented is True


# ---------------------------------------------------------------------------
# load_register — CSV
# ---------------------------------------------------------------------------

def _write_csv(tmp_path, rows: list[dict]) -> pathlib.Path:
    path = tmp_path / "register.csv"
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return path


def test_load_register_csv(tmp_path):
    rows = [{
        "entry_id": "REG-001",
        "supplier_name": "Acme A/S",
        "supplier_country": "DK",
        "service_type": "cloud_hosting",
        "service_description": "Cloud",
        "contract_reference": "C-001",
        "classification": "critical_important",
        "functions_supported": "core_banking,data_storage",
        "exit_strategy_documented": "true",
        "notes": "test",
    }]
    path = _write_csv(tmp_path, rows)
    entries = load_register(path)
    assert len(entries) == 1
    assert entries[0].functions_supported == ["core_banking", "data_storage"]
    assert entries[0].exit_strategy_documented is True


def test_load_register_csv_bool_false_string(tmp_path):
    rows = [{
        "entry_id": "REG-001",
        "supplier_name": "Acme A/S",
        "supplier_country": "DK",
        "service_type": "cloud_hosting",
        "service_description": "Cloud",
        "contract_reference": "C-001",
        "classification": "critical_important",
        "exit_strategy_documented": "false",
    }]
    path = _write_csv(tmp_path, rows)
    entries = load_register(path)
    assert entries[0].exit_strategy_documented is False


def test_load_register_missing_file():
    with pytest.raises(FileNotFoundError):
        load_register("/nonexistent/path/register.yaml")


def test_load_register_unsupported_format(tmp_path):
    path = tmp_path / "register.txt"
    path.write_text("data", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported"):
        load_register(path)


# ---------------------------------------------------------------------------
# load_register — sample YAML from project
# ---------------------------------------------------------------------------

SAMPLE_REGISTER = pathlib.Path(__file__).parent.parent / "synthetic_contracts" / "register_sample.yaml"


def test_load_sample_register():
    entries = load_register(SAMPLE_REGISTER)
    assert len(entries) == 6


def test_sample_register_has_critical_entries():
    entries = load_register(SAMPLE_REGISTER)
    critical = [e for e in entries if e.classification == "critical_important"]
    assert len(critical) == 5


def test_sample_register_has_no_contract_entry():
    entries = load_register(SAMPLE_REGISTER)
    no_match = [e for e in entries if e.contract_reference == "NNS-NET-2021-007"]
    assert len(no_match) == 1


# ---------------------------------------------------------------------------
# reconcile — exact contract reference match
# ---------------------------------------------------------------------------

def test_reconcile_matches_by_contract_reference():
    register = [_entry(contract_reference="CONTRACT-001")]
    analyses = [_analysis(contract_id="CONTRACT-001")]
    report = reconcile(register, analyses)
    assert len(report.matched) == 1
    assert len(report.register_only) == 0
    assert len(report.analysis_only) == 0


def test_reconcile_match_method_contract_reference():
    register = [_entry(contract_reference="CONTRACT-001")]
    analyses = [_analysis(contract_id="CONTRACT-001")]
    report = reconcile(register, analyses)
    assert report.matched[0].match_method == "contract_reference"


def test_reconcile_matched_entry_fields():
    register = [_entry(supplier_name="Acme A/S", contract_reference="C-001", classification="critical_important")]
    analyses = [_analysis(
        analysis_id="SA-001",
        contract_path="contract.md",
        supplier_name="Acme A/S",
        overall_status="gaps_found",
        missing=3,
        satisfied=7,
        contract_id="C-001",
    )]
    report = reconcile(register, analyses)
    m = report.matched[0]
    assert m.analysis_id == "SA-001"
    assert m.contract_path == "contract.md"
    assert m.supplier_name_in_contract == "Acme A/S"
    assert m.overall_status == "gaps_found"
    assert m.missing_count == 3
    assert m.satisfied_count == 7


# ---------------------------------------------------------------------------
# reconcile — supplier name fallback
# ---------------------------------------------------------------------------

def test_reconcile_matches_by_supplier_name():
    register = [_entry(supplier_name="Acme A/S", contract_reference="WRONG-REF")]
    analyses = [_analysis(supplier_name="Acme A/S", contract_id="SA-001")]
    report = reconcile(register, analyses)
    assert len(report.matched) == 1
    assert report.matched[0].match_method == "supplier_name"


def test_reconcile_supplier_name_normalisation():
    register = [_entry(supplier_name="Acme A/S", contract_reference="WRONG-REF")]
    # Slight capitalisation difference — normalised, should match
    analyses = [_analysis(supplier_name="acme a/s", contract_id="SA-001")]
    report = reconcile(register, analyses)
    assert len(report.matched) == 1


# ---------------------------------------------------------------------------
# reconcile — register_only and analysis_only
# ---------------------------------------------------------------------------

def test_reconcile_register_only_when_no_analysis():
    register = [_entry()]
    report = reconcile(register, [])
    assert len(report.register_only) == 1
    assert len(report.matched) == 0
    assert len(report.analysis_only) == 0


def test_reconcile_analysis_only_when_no_register_match():
    report = reconcile([], [_analysis()])
    assert len(report.analysis_only) == 1
    assert len(report.matched) == 0
    assert len(report.register_only) == 0


def test_reconcile_analysis_only_has_contract_path():
    report = reconcile([], [_analysis(contract_path="path/to/contract.md")])
    assert report.analysis_only[0]["contract_path"] == "path/to/contract.md"


def test_reconcile_mixed():
    register = [
        _entry("REG-001", "Acme A/S", "C-001"),
        _entry("REG-002", "Beta BV", "C-002"),
    ]
    analyses = [
        _analysis("SA-001", contract_id="C-001"),
        _analysis("SA-003", contract_id="C-999"),  # no register entry
    ]
    report = reconcile(register, analyses)
    assert len(report.matched) == 1
    assert len(report.register_only) == 1
    assert report.register_only[0].entry_id == "REG-002"
    assert len(report.analysis_only) == 1


# ---------------------------------------------------------------------------
# reconcile — summary stats
# ---------------------------------------------------------------------------

def test_reconcile_summary_counts():
    register = [_entry(), _entry("REG-002", "Other", "C-999")]
    analyses = [_analysis()]
    report = reconcile(register, analyses)
    s = report.summary
    assert s["total_register_entries"] == 2
    assert s["total_analyses"] == 1
    assert s["matched"] == 1
    assert s["register_only"] == 1
    assert s["analysis_only"] == 0


def test_reconcile_summary_critical_important_counts():
    register = [
        _entry("REG-001", "Acme", "C-001", classification="critical_important"),
        _entry("REG-002", "Beta", "C-002", classification="non_critical"),
    ]
    analyses = [
        _analysis("SA-001", missing=2, contract_id="C-001"),
        _analysis("SA-002", missing=0, contract_id="C-002"),
    ]
    report = reconcile(register, analyses)
    assert report.summary["critical_important_matched"] == 1
    assert report.summary["critical_important_with_gaps"] == 1


def test_reconcile_customer_name():
    report = reconcile([], [], customer_name="Nordbank A/S")
    assert report.customer_name == "Nordbank A/S"


# ---------------------------------------------------------------------------
# reconcile_from_pipeline_results
# ---------------------------------------------------------------------------

class _FakePipelineResult:
    def __init__(self):
        self.analysis_id = "SA-001"
        self.contract_path = "contract.md"
        self.supplier_name = "Acme A/S"
        self.overall_status = "gaps_found"
        self.summary = {"missing": 2, "satisfied": 5}


def test_reconcile_from_pipeline_results():
    register = [_entry()]
    pr = _FakePipelineResult()
    report = reconcile_from_pipeline_results(register, [pr])
    assert len(report.matched) == 1


def test_reconcile_from_pipeline_results_uses_analysis_id_as_contract_id():
    register = [_entry(contract_reference="SA-001")]
    pr = _FakePipelineResult()
    report = reconcile_from_pipeline_results(register, [pr])
    assert report.matched[0].match_method == "contract_reference"


# ---------------------------------------------------------------------------
# ReconciliationReport.to_json
# ---------------------------------------------------------------------------

def test_to_json_is_valid_json():
    report = reconcile([_entry()], [_analysis()])
    parsed = json.loads(report.to_json())
    assert "matched" in parsed
    assert "summary" in parsed


def test_to_json_summary_fields():
    report = reconcile([_entry()], [_analysis()])
    parsed = json.loads(report.to_json())
    assert "total_register_entries" in parsed["summary"]


# ---------------------------------------------------------------------------
# ReconciliationReport.to_markdown
# ---------------------------------------------------------------------------

def test_to_markdown_contains_customer_name():
    report = reconcile([_entry()], [], customer_name="TestBank")
    md = report.to_markdown()
    assert "TestBank" in md


def test_to_markdown_contains_advisory_note():
    report = reconcile([], [])
    md = report.to_markdown()
    assert "human review" in md.lower()


def test_to_markdown_matched_section():
    report = reconcile([_entry(supplier_name="Acme A/S")], [_analysis()])
    md = report.to_markdown()
    assert "Matched" in md
    assert "Acme A/S" in md


def test_to_markdown_register_only_section():
    report = reconcile([_entry(supplier_name="Orphan Supplier")], [])
    md = report.to_markdown()
    assert "Register Entries Without" in md
    assert "Orphan Supplier" in md


def test_to_markdown_analysis_only_section():
    report = reconcile([], [_analysis(contract_path="path/to/unregistered.md")])
    md = report.to_markdown()
    assert "Contracts Not in Register" in md
    assert "unregistered.md" in md


# ---------------------------------------------------------------------------
# save_report
# ---------------------------------------------------------------------------

def test_save_report_creates_files(tmp_path):
    report = reconcile([_entry()], [_analysis()])
    paths = save_report(report, tmp_path)
    assert paths["json"].exists()
    assert paths["markdown"].exists()


def test_save_report_json_valid(tmp_path):
    report = reconcile([_entry()], [_analysis()])
    paths = save_report(report, tmp_path)
    parsed = json.loads(paths["json"].read_text())
    assert "matched" in parsed


def test_save_report_custom_stem(tmp_path):
    report = reconcile([], [])
    paths = save_report(report, tmp_path, stem="my-report")
    assert paths["json"].name == "my-report.json"
    assert paths["markdown"].name == "my-report.md"


def test_save_report_creates_output_dir(tmp_path):
    new_dir = tmp_path / "sub" / "dir"
    report = reconcile([], [])
    paths = save_report(report, new_dir)
    assert paths["json"].exists()


# ---------------------------------------------------------------------------
# Integration: sample register + synthetic analyses
# ---------------------------------------------------------------------------

def test_sample_register_reconciliation():
    """
    Load the 6-entry sample register and reconcile against 5 analyses
    matching the synthetic contracts. REG-006 (Nordic Network Services) has
    no matching analysis and should appear in register_only.
    """
    entries = load_register(SAMPLE_REGISTER)
    analyses = [
        {"analysis_id": "SA-01", "contract_path": "contract_01.md",
         "supplier_name": "NordCloud Hosting A/S", "overall_status": "gaps_found",
         "summary": {"missing": 3, "satisfied": 10}, "contract_id": "NBS-ICT-2023-0047"},
        {"analysis_id": "SA-02", "contract_path": "contract_02.md",
         "supplier_name": "PayLink Technologies Ltd", "overall_status": "gaps_found",
         "summary": {"missing": 5, "satisfied": 8}, "contract_id": "FIN-PP-2022-091"},
        {"analysis_id": "SA-03", "contract_path": "contract_03.md",
         "supplier_name": "DataCenter DK ApS", "overall_status": "gaps_found",
         "summary": {"missing": 4, "satisfied": 7}, "contract_id": "KAP-IT-2018-003"},
        {"analysis_id": "SA-04", "contract_path": "contract_04.md",
         "supplier_name": "ComplianceTools International BV", "overall_status": "gaps_found",
         "summary": {"missing": 2, "satisfied": 9}, "contract_id": "CT-SAAS-2024-0012"},
        {"analysis_id": "SA-05", "contract_path": "contract_05.md",
         "supplier_name": "SecureVault Nordic A/S", "overall_status": "compliant",
         "summary": {"missing": 0, "satisfied": 13}, "contract_id": "NBI-ICT-2024-DOR-001"},
    ]
    report = reconcile(entries, analyses, customer_name="Integration Test")
    assert report.summary["matched"] == 5
    assert report.summary["register_only"] == 1
    assert report.summary["analysis_only"] == 0
    assert report.register_only[0].supplier_name == "Nordic Network Services A/S"
    # critical_important: REG-001..003, REG-005 = 4 entries
    assert report.summary["critical_important_matched"] == 4
