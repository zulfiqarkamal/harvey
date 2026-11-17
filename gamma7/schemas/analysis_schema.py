"""
Gamma7 Evidence & Compliance Engine — Analysis Schema
======================================================
Defines the data structures for contract analysis output.
Used by: document ingestion, gap analysis, remediation workflow.

This schema is intentionally simple for Phase 1 MVP.
No ORM, no external dependencies — plain Python dataclasses + JSON serialization.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
import json
import datetime


class ComplianceStatus(str, Enum):
    SATISFIED = "satisfied"       # Requirement clearly met with cited evidence
    MISSING = "missing"           # Requirement not addressed in contract
    AMBIGUOUS = "ambiguous"       # Partial coverage — needs legal review
    NOT_APPLICABLE = "na"         # Requirement does not apply (documented rationale)
    NOT_ASSESSED = "not_assessed" # Analysis not yet performed


# ---------------------------------------------------------------------------
# Risk matrix — ISACA/IIA/COSO 3×3 (Likelihood × Impact)
# ---------------------------------------------------------------------------

class RiskRating(str, Enum):
    LOW = "low"       # Score 1–2
    MEDIUM = "medium" # Score 3–4
    HIGH = "high"     # Score 6–9


class ControlRisk(str, Enum):
    """Effectiveness of controls mitigating inherent risk."""
    LOW = "low"       # Controls effective and tested — reduces inherent risk ≥60 %
    MEDIUM = "medium" # Controls exist but gaps — reduces 30–59 %
    HIGH = "high"     # Controls missing / not implemented — reduces <30 %


@dataclass
class RiskScore:
    """3×3 risk matrix score (Likelihood 1–3 × Impact 1–3)."""
    likelihood: int    # 1=Low, 2=Medium, 3=High
    impact: int        # 1=Low, 2=Medium, 3=High
    score: int         # likelihood × impact (1–9)
    rating: RiskRating

    def __post_init__(self) -> None:
        if self.likelihood not in (1, 2, 3):
            raise ValueError(f"likelihood must be 1–3, got {self.likelihood}")
        if self.impact not in (1, 2, 3):
            raise ValueError(f"impact must be 1–3, got {self.impact}")

    @classmethod
    def from_components(cls, likelihood: int, impact: int) -> "RiskScore":
        score = likelihood * impact
        if score <= 2:
            rating = RiskRating.LOW
        elif score <= 4:
            rating = RiskRating.MEDIUM
        else:
            rating = RiskRating.HIGH
        return cls(likelihood=likelihood, impact=impact, score=score, rating=rating)


# ---------------------------------------------------------------------------
# Control effectiveness and residual risk
# ---------------------------------------------------------------------------

_CONTROL_EFFECTIVENESS_REDUCTION = {
    ControlRisk.LOW: 0.65,    # ≥60 % — use midpoint 65 %
    ControlRisk.MEDIUM: 0.45, # 30–59 % — use midpoint 45 %
    ControlRisk.HIGH: 0.10,   # <30 % — use 10 %
}


def residual_score(inherent: RiskScore, control_risk: ControlRisk) -> float:
    """Residual risk = inherent score × (1 − control effectiveness)."""
    reduction = _CONTROL_EFFECTIVENESS_REDUCTION[control_risk]
    return round(inherent.score * (1.0 - reduction), 2)


def residual_rating(residual: float) -> RiskRating:
    if residual <= 2.0:
        return RiskRating.LOW
    elif residual <= 4.0:
        return RiskRating.MEDIUM
    return RiskRating.HIGH


class RiskLevel(str, Enum):
    CRITICAL = "critical"   # Must remediate before DORA deadline / active regulatory risk
    HIGH = "high"           # Significant gap, remediate within 30 days
    MEDIUM = "medium"       # Gap present, remediate within 90 days
    LOW = "low"             # Minor or informational finding
    INFO = "info"           # Observation, no action required


class FunctionClassification(str, Enum):
    CRITICAL_IMPORTANT = "critical_important"
    NON_CRITICAL = "non_critical"
    UNDETERMINED = "undetermined"


@dataclass
class EvidencePassage:
    """A specific passage in the contract that addresses a requirement."""
    page: Optional[int]
    section: Optional[str]       # e.g. "Section 8.2" or "Clause 15"
    text: str                     # Exact quoted text from contract
    relevance_note: str           # Why this passage satisfies (or partially satisfies) the requirement


@dataclass
class RequirementAssessment:
    """Assessment of a single requirement against a contract."""
    requirement_id: str           # e.g. "ART30-001"
    requirement_article: str      # e.g. "Art. 30(2)(a)"
    requirement_summary: str      # Short description of what was assessed
    status: ComplianceStatus
    risk_level: Optional[RiskLevel] = None
    evidence: list[EvidencePassage] = field(default_factory=list)
    gap_description: Optional[str] = None    # What is missing or ambiguous
    remediation_suggestion: str = ""         # Suggested contractual language or action
    na_rationale: Optional[str] = None       # Required if status = NOT_APPLICABLE
    assessed_by: str = "gamma7-engine-v1"
    assessed_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


@dataclass
class ContractMetadata:
    """Metadata extracted from or provided about the contract."""
    contract_id: str              # Internal reference
    supplier_name: str
    supplier_lei: Optional[str] = None
    contract_title: Optional[str] = None
    contract_date: Optional[str] = None      # ISO date string
    contract_version: Optional[str] = None
    function_classification: FunctionClassification = FunctionClassification.UNDETERMINED
    service_category: Optional[str] = None  # e.g. "Cloud hosting", "Payment processing"
    data_processing_locations: list[str] = field(default_factory=list)
    third_country_provider: Optional[bool] = None
    source_file: Optional[str] = None       # Filename of ingested document


@dataclass
class ContractAnalysisResult:
    """
    Complete analysis result for one contract against one regulatory framework.
    This is the primary output artifact of the Gamma7 engine.
    """
    analysis_id: str
    framework: str               # e.g. "DORA_ART30", "GDPR_ART28"
    contract: ContractMetadata
    assessments: list[RequirementAssessment] = field(default_factory=list)
    overall_status: ComplianceStatus = ComplianceStatus.NOT_ASSESSED
    summary_stats: dict = field(default_factory=dict)
    remediation_tasks: list[RemediationTask] = field(default_factory=list)
    analysis_version: str = "1.0"
    analysed_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    human_reviewed: bool = False
    human_reviewer: Optional[str] = None
    human_reviewed_at: Optional[str] = None

    def compute_summary(self) -> dict:
        """Compute summary statistics from assessments."""
        counts = {s.value: 0 for s in ComplianceStatus}
        for a in self.assessments:
            counts[a.status.value] += 1
        total_applicable = len([a for a in self.assessments
                                 if a.status != ComplianceStatus.NOT_APPLICABLE])
        satisfied = counts[ComplianceStatus.SATISFIED.value]
        compliance_pct = round(satisfied / total_applicable * 100, 1) if total_applicable else 0
        self.summary_stats = {
            "total_requirements": len(self.assessments),
            "satisfied": counts[ComplianceStatus.SATISFIED.value],
            "missing": counts[ComplianceStatus.MISSING.value],
            "ambiguous": counts[ComplianceStatus.AMBIGUOUS.value],
            "not_applicable": counts[ComplianceStatus.NOT_APPLICABLE.value],
            "not_assessed": counts[ComplianceStatus.NOT_ASSESSED.value],
            "compliance_percentage": compliance_pct,
            "critical_gaps": len([a for a in self.assessments
                                   if a.risk_level == RiskLevel.CRITICAL]),
            "high_gaps": len([a for a in self.assessments
                               if a.risk_level == RiskLevel.HIGH]),
        }
        # Determine overall status
        if counts[ComplianceStatus.NOT_ASSESSED.value] > 0:
            self.overall_status = ComplianceStatus.NOT_ASSESSED
        elif counts[ComplianceStatus.MISSING.value] > 0:
            self.overall_status = ComplianceStatus.MISSING
        elif counts[ComplianceStatus.AMBIGUOUS.value] > 0:
            self.overall_status = ComplianceStatus.AMBIGUOUS
        else:
            self.overall_status = ComplianceStatus.SATISFIED
        return self.summary_stats

    def to_json(self, indent: int = 2) -> str:
        self.compute_summary()
        return json.dumps(asdict(self), indent=indent, default=str)


@dataclass
class RemediationTask:
    """A single remediation action derived from a gap finding."""
    task_id: str
    requirement_id: str          # Links back to RequirementAssessment
    priority: RiskLevel
    title: str
    description: str
    suggested_contract_language: Optional[str] = None  # Draft amendment text
    owner: Optional[str] = None  # e.g. "Legal", "Supplier", "Internal"
    due_date: Optional[str] = None
    status: str = "open"         # open / in_progress / resolved / accepted_risk
    resolution_note: Optional[str] = None


@dataclass
class RegisterEntry:
    """
    Entry in the financial entity's ICT Third-Party Register.
    Maps to DORA Art. 28(3) register requirements.
    """
    supplier_name: str
    supplier_lei: Optional[str] = None
    contract_reference: Optional[str] = None
    contract_date: Optional[str] = None
    service_category: Optional[str] = None
    critical_important: bool = False
    third_country_provider: bool = False
    sub_contractors: list[str] = field(default_factory=list)
    latest_assessment_id: Optional[str] = None
    latest_assessment_date: Optional[str] = None
    overall_status: ComplianceStatus = ComplianceStatus.NOT_ASSESSED
    open_remediation_tasks: int = 0
    notes: Optional[str] = None
