"""
Gamma7 Gap Analysis Engine — v1.0
Analyses ICT contracts against DORA Art. 30 and GDPR Art. 28 requirement libraries.

Usage:
    from gamma7.engine.analyser import ContractAnalyser
    analyser = ContractAnalyser()
    result = analyser.analyse(contract_text, classification="critical_important")
    print(result.to_json())

Standalone:
    python analyser.py contract.md [--classification critical_important] [--validate expected.yaml]
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import textwrap
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional

import yaml

class ComplianceStatus(str, Enum):
    SATISFIED = "satisfied"
    EVIDENCE_FOUND = "evidence_found"  # evidence phrases detected — human review required
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    NOT_APPLICABLE = "na"
    NOT_ASSESSED = "not_assessed"


class RiskLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class EvidencePassage:
    section: Optional[str]
    text: str
    relevance_note: str
    page: Optional[int] = None


@dataclass
class RequirementAssessment:
    requirement_id: str
    requirement_article: str
    requirement_summary: str
    status: ComplianceStatus
    risk_level: Optional[RiskLevel] = None
    evidence: list = field(default_factory=list)
    gap_description: Optional[str] = None
    remediation_suggestion: str = ""
    na_rationale: Optional[str] = None
    assessed_by: str = "gamma7-engine-v1"
    assessed_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


@dataclass
class ContractMetadata:
    contract_id: str
    supplier_name: str
    customer_name: str
    classification: str
    contract_date: Optional[str] = None
    file_name: Optional[str] = None


@dataclass
class RemediationTask:
    requirement_id: str
    priority: str
    action: str
    suggested_clause: Optional[str] = None


@dataclass
class ContractAnalysisResult:
    analysis_id: str
    framework: str
    contract: ContractMetadata
    assessments: list
    overall_status: ComplianceStatus = ComplianceStatus.NOT_ASSESSED
    summary_stats: dict = field(default_factory=dict)
    remediation_tasks: list = field(default_factory=list)

    def compute_summary(self) -> None:
        counts = {s.value: 0 for s in ComplianceStatus}
        for a in self.assessments:
            counts[a.status.value] += 1
        self.summary_stats = counts
        if counts[ComplianceStatus.MISSING.value] > 0:
            self.overall_status = ComplianceStatus.MISSING
        elif counts[ComplianceStatus.AMBIGUOUS.value] > 0:
            self.overall_status = ComplianceStatus.AMBIGUOUS
        elif counts.get(ComplianceStatus.EVIDENCE_FOUND.value, 0) > 0 or counts[ComplianceStatus.NOT_ASSESSED.value] > 0:
            self.overall_status = ComplianceStatus.NOT_ASSESSED
        else:
            self.overall_status = ComplianceStatus.SATISFIED

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)


REPO_ROOT = pathlib.Path(__file__).parent.parent
REQUIREMENTS_DIR = REPO_ROOT / "requirements"


def load_requirements(framework: str = "dora_art30") -> list[dict]:
    path = REQUIREMENTS_DIR / f"{framework}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Requirements file not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("requirements", [])


SCOPE_MAP = {
    "all": {"critical_important", "non_critical"},
    "critical_only": {"critical_important"},
    "conditional": {"critical_important", "non_critical"},  # assessed only when evidence of trigger condition found
}


def _normalise(text: str) -> str:
    # Strip markdown bold/italic markers before lowercasing so **word** doesn't break phrase matching
    text = re.sub(r'\*\*|__', '', text)
    text = re.sub(r'(?<!\w)\*(?!\*)(?!\w)', '', text)
    return re.sub(r"\s+", " ", text.lower().strip())


def _keyword_hits(contract_norm: str, keywords: list[str]) -> list[str]:
    found = []
    for kw in keywords:
        pattern = re.escape(kw.lower())
        if re.search(pattern, contract_norm):
            found.append(kw)
    return found


def _phrase_hits(contract_norm: str, phrases: list[str]) -> list[str]:
    """Phrase-level literal matching — no word decomposition. Supports basic .* wildcards."""
    found = []
    for phrase in phrases:
        # Allow .* as a simple wildcard (for patterns like "notify.*within")
        if ".*" in phrase:
            pattern = phrase.lower()
        else:
            pattern = re.escape(phrase.lower())
        if re.search(pattern, contract_norm):
            found.append(phrase)
    return found


def _extract_passage(contract_text: str, keyword: str, context_chars: int = 200) -> Optional[EvidencePassage]:
    idx = contract_text.lower().find(keyword.lower())
    if idx == -1:
        return None
    start = max(0, idx - context_chars // 2)
    end = min(len(contract_text), idx + len(keyword) + context_chars // 2)
    snippet = contract_text[start:end].strip()
    section = None
    heading_match = re.findall(r"#{1,3}\s+(.+)", contract_text[:idx])
    if heading_match:
        section = heading_match[-1].strip()
    return EvidencePassage(section=section, text=snippet, relevance_note=f"Contains keyword: '{keyword}'")


def assess_requirement(
    req: dict,
    contract_text: str,
    contract_norm: str,
    classification: str,
) -> RequirementAssessment:
    req_id = req["id"]
    scope = req.get("scope", "all")
    allowed_classifications = SCOPE_MAP.get(scope, {"critical_important", "non_critical"})
    if classification not in allowed_classifications:
        return RequirementAssessment(
            requirement_id=req_id,
            requirement_article=req.get("article", ""),
            requirement_summary=req.get("text_en", "")[:120],
            status=ComplianceStatus.NOT_APPLICABLE,
            na_rationale=f"Requirement scope '{scope}' does not apply to '{classification}' contracts.",
        )

    evidence_criteria: list[str] = req.get("evidence_criteria", [])
    gap_indicators: list[str] = req.get("gap_indicators", [])

    # Use phrase-level match_terms when available; fall back to flatten_terms decomposition
    match_terms_evidence: Optional[list[str]] = req.get("match_terms_evidence")
    match_terms_gap: Optional[list[str]] = req.get("match_terms_gap")

    if match_terms_evidence is not None:
        evidence_hits = _phrase_hits(contract_norm, match_terms_evidence)
        gap_hits = _phrase_hits(contract_norm, match_terms_gap or [])
    else:
        def flatten_terms(phrases: list[str]) -> list[str]:
            result = []
            for p in phrases:
                result.append(p.lower())
                words = [w for w in p.lower().split() if len(w) > 4]
                result.extend(words)
            return list(dict.fromkeys(result))

        evidence_terms = flatten_terms(evidence_criteria)
        gap_terms = flatten_terms(gap_indicators)
        evidence_hits = _keyword_hits(contract_norm, evidence_terms)
        gap_hits = _keyword_hits(contract_norm, gap_terms)

    passages = []
    for kw in evidence_hits[:3]:
        passage = _extract_passage(contract_text, kw)
        if passage:
            passages.append(passage)

    evidence_score = len(evidence_hits)
    gap_score = len(gap_hits)

    if evidence_score == 0 and gap_score == 0:
        if scope == "conditional":
            # Conditional requirement: no trigger phrases detected — applicability uncertain (not confirmed absent)
            return RequirementAssessment(
                requirement_id=req_id,
                requirement_article=req.get("article", ""),
                requirement_summary=req.get("text_en", "")[:120],
                status=ComplianceStatus.NOT_ASSESSED,
                na_rationale="Trigger condition not detected in contract — applicability uncertain. Human review required.",
            )
        # Closed-world assumption: absence of compliance language = gap requiring review
        status = ComplianceStatus.MISSING
        gap_desc = "No relevant clauses detected in contract. Requirement appears absent."
        risk = _default_risk(req, classification)
    elif gap_score > 0 and evidence_score == 0:
        status = ComplianceStatus.MISSING
        gap_desc = f"Gap indicators found: {', '.join(gap_hits[:3])}. No satisfying evidence detected."
        risk = _default_risk(req, classification)
    elif gap_score > 0 and evidence_score > 0:
        status = ComplianceStatus.AMBIGUOUS
        gap_desc = (
            f"Partial evidence found ({', '.join(evidence_hits[:2])}) "
            f"but gap indicators also detected ({', '.join(gap_hits[:2])}). "
            "Clause may be insufficient — manual review required."
        )
        risk = _default_risk(req, classification)
    else:
        # Evidence phrases found, no gap indicators — conservative: flag for human review
        status = ComplianceStatus.EVIDENCE_FOUND
        gap_desc = (
            f"Evidence candidates found ({', '.join(evidence_hits[:3])}). "
            "Keyword matching cannot confirm compliance — human review of clause text required."
        )
        risk = None

    remediation = ""
    if status in (ComplianceStatus.MISSING, ComplianceStatus.AMBIGUOUS):
        remediation = _build_remediation(req, status)

    return RequirementAssessment(
        requirement_id=req_id,
        requirement_article=req.get("article", ""),
        requirement_summary=req.get("text_en", "")[:120],
        status=status,
        risk_level=risk,
        evidence=[asdict(p) for p in passages],
        gap_description=gap_desc,
        remediation_suggestion=remediation,
    )


def _default_risk(req: dict, classification: str) -> RiskLevel:
    obligation = req.get("obligation_level", "should")
    scope = req.get("scope", "all")
    if obligation == "must" and scope == "critical_only":
        return RiskLevel.HIGH
    if obligation == "must":
        base = RiskLevel.HIGH if classification == "critical_important" else RiskLevel.MEDIUM
        return base
    return RiskLevel.MEDIUM


def _build_remediation(req: dict, status: ComplianceStatus) -> str:
    req_id = req["id"]
    obligation = req.get("obligation_level", "should")
    verb = "Add" if status == ComplianceStatus.MISSING else "Strengthen"
    return (
        f"{verb} a contractual clause addressing {req_id} ({req.get('category', 'requirement')}). "
        f"Obligation level: {obligation}. "
        f"Evidence criteria: {'; '.join(req.get('evidence_criteria', [])[:3])}."
    )


class ContractAnalyser:
    def __init__(self, frameworks: Optional[list[str]] = None):
        self.frameworks = frameworks or ["dora_art30", "gdpr_art28"]
        self._requirements: dict[str, list[dict]] = {}
        for fw in self.frameworks:
            self._requirements[fw] = load_requirements(fw)

    def analyse(
        self,
        contract_text: str,
        classification: str = "non_critical",
        contract_metadata: Optional[ContractMetadata] = None,
    ) -> ContractAnalysisResult:
        if classification not in ("critical_important", "non_critical"):
            raise ValueError(f"classification must be 'critical_important' or 'non_critical', got: {classification!r}")
        contract_norm = _normalise(contract_text)
        if contract_metadata is None:
            contract_metadata = ContractMetadata(
                contract_id=f"contract-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}",
                supplier_name="Unknown",
                customer_name="Unknown",
                classification=classification,
            )
        all_assessments: list[RequirementAssessment] = []
        all_tasks: list[RemediationTask] = []
        for fw in self.frameworks:
            for req in self._requirements[fw]:
                assessment = assess_requirement(req, contract_text, contract_norm, classification)
                all_assessments.append(assessment)
                if assessment.status in (ComplianceStatus.MISSING, ComplianceStatus.AMBIGUOUS):
                    priority = _remediation_priority(assessment)
                    all_tasks.append(RemediationTask(
                        requirement_id=assessment.requirement_id,
                        priority=priority,
                        action=assessment.remediation_suggestion,
                    ))
        result = ContractAnalysisResult(
            analysis_id=f"GA-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')}",
            framework="+".join(fw.upper() for fw in self.frameworks),
            contract=contract_metadata,
            assessments=all_assessments,
            remediation_tasks=all_tasks,
        )
        result.compute_summary()
        return result

    def analyse_file(self, path: str | pathlib.Path, **kwargs) -> ContractAnalysisResult:
        path = pathlib.Path(path)
        text = path.read_text(encoding="utf-8")
        if "contract_metadata" not in kwargs:
            kwargs["contract_metadata"] = _metadata_from_filename(path, kwargs.get("classification", "non_critical"))
        return self.analyse(text, **kwargs)


def _remediation_priority(assessment: RequirementAssessment) -> str:
    if assessment.risk_level in (RiskLevel.CRITICAL, RiskLevel.HIGH):
        return "immediate"
    if assessment.risk_level == RiskLevel.MEDIUM:
        return "short_term"
    return "medium_term"


def _metadata_from_filename(path: pathlib.Path, classification: str) -> ContractMetadata:
    return ContractMetadata(
        contract_id=path.stem,
        supplier_name="(from file)",
        customer_name="(from file)",
        classification=classification,
        file_name=path.name,
    )


def validate_against_expected(
    result: ContractAnalysisResult,
    expected: dict,
    contract_key: str,
) -> dict:
    contract_expected = expected.get(contract_key, {})
    all_expected = {
        item["requirement_id"]: item
        for item in contract_expected.get("expected_gaps", [])
        + contract_expected.get("gdpr_findings", [])
    }
    # Separate genuine gaps from N/A expectations
    genuine_expected = {k: v for k, v in all_expected.items() if v.get("expected_status") != "na"}
    na_expected = {k: v for k, v in all_expected.items() if v.get("expected_status") == "na"}

    # Active-gap findings: only MISSING/AMBIGUOUS qualify; SATISFIED/EVIDENCE_FOUND/NOT_APPLICABLE/NOT_ASSESSED are excluded
    _excluded = (
        ComplianceStatus.SATISFIED,
        ComplianceStatus.EVIDENCE_FOUND,
        ComplianceStatus.NOT_ASSESSED,
        ComplianceStatus.NOT_APPLICABLE,
    )
    result_map = {
        a.requirement_id: a
        for a in result.assessments
        if a.status not in _excluded
    }

    matched = []
    missed = []
    false_positives = []

    for req_id, exp in genuine_expected.items():
        exp_status = exp["expected_status"]
        if req_id in result_map:
            actual_status = result_map[req_id].status.value
            if _status_match(actual_status, exp_status):
                matched.append(req_id)
            else:
                missed.append({"id": req_id, "expected": exp_status, "actual": actual_status})
        else:
            actual = next((a for a in result.assessments if a.requirement_id == req_id), None)
            actual_status = actual.status.value if actual else "not_found"
            missed.append({"id": req_id, "expected": exp_status, "actual": actual_status})

    # Check N/A expectations are resolved correctly (should be NOT_APPLICABLE or NOT_ASSESSED)
    na_matched = []
    na_missed = []
    _na_ok = (ComplianceStatus.NOT_APPLICABLE, ComplianceStatus.NOT_ASSESSED)
    for req_id in na_expected:
        actual = next((a for a in result.assessments if a.requirement_id == req_id), None)
        if actual and actual.status in _na_ok:
            na_matched.append(req_id)
        else:
            na_missed.append({
                "id": req_id,
                "expected": "na",
                "actual": actual.status.value if actual else "not_found",
            })

    # FP = active-gap findings not in genuine_expected
    for req_id in result_map:
        if req_id not in genuine_expected:
            false_positives.append({"id": req_id, "actual_status": result_map[req_id].status.value})

    total_genuine = len(genuine_expected)
    accuracy_pct = round(len(matched) / total_genuine * 100, 1) if total_genuine > 0 else 100.0
    return {
        "contract_key": contract_key,
        "expected_genuine_gaps": total_genuine,
        "na_expectations": len(na_expected),
        "matched": len(matched),
        "na_matched": len(na_matched),
        "missed": missed,
        "na_missed": na_missed,
        "false_positives": false_positives,
        "accuracy_pct": accuracy_pct,
    }


def _status_match(actual: str, expected: str) -> bool:
    if actual == expected:
        return True
    if expected in ("missing", "ambiguous") and actual in ("missing", "ambiguous"):
        return True
    return False


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Gamma7 Gap Analysis Engine")
    p.add_argument("contract", help="Path to contract file")
    p.add_argument("--classification", choices=["critical_important", "non_critical"], default="non_critical")
    p.add_argument("--validate", metavar="EXPECTED_YAML")
    p.add_argument("--contract-key", metavar="KEY")
    p.add_argument("--output")
    args = p.parse_args()
    analyser = ContractAnalyser()
    result = analyser.analyse_file(args.contract, classification=args.classification)
    output_json = result.to_json()
    if args.output:
        pathlib.Path(args.output).write_text(output_json, encoding="utf-8")
    else:
        print(output_json)
    if args.validate and args.contract_key:
        import yaml as _yaml
        with open(args.validate, encoding="utf-8") as f:
            expected = _yaml.safe_load(f)
        report = validate_against_expected(result, expected, args.contract_key)
        print(f"\nValidation: {report['accuracy_pct']}% ({report['matched']}/{report['expected_genuine_gaps']} gaps detected)", file=sys.stderr)
