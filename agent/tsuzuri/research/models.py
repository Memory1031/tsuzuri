"""Pure data models for the research runtime.

Split by who owns them:

- runtime-owned records (Observation / SourceCandidate / Evidence /
  Finding / FindingConflict) carry provenance the model never fills;
- model-output schemas (extraction / consolidation / sufficiency /
  synthesis) constrain what the LLM may decide;
- run bookkeeping (warnings / metrics / round results) describes the
  run itself.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai.usage import RunUsage


class SourceKind(StrEnum):
    STRUCTURED_API = "structured_api"
    WEB_SEARCH_SNIPPET = "web_search_snippet"
    WEB_PAGE = "web_page"


class ResearchMode(StrEnum):
    FAST = "fast"
    GROUNDED = "grounded"
    DEEP = "deep"


class ObservationStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"


class ResearchSufficiencyStatus(StrEnum):
    SUFFICIENT = "sufficient"
    SUFFICIENT_WITH_CONFLICT = "sufficient_with_conflict"
    NEEDS_MORE_RESEARCH = "needs_more_research"


class ResearchGapKind(StrEnum):
    MISSING_COVERAGE = "missing_coverage"
    UNRESOLVED_CONFLICT = "unresolved_conflict"


@dataclass
class SourceCandidate:
    id: str
    kind: SourceKind
    observation_id: str

    source_name: str
    source_url: str | None = None
    source_id: str | None = None

    content: object | None = None


@dataclass
class Observation:
    id: str
    tool_name: str
    arguments: dict[str, object]
    status: ObservationStatus
    result: object | None = None
    error: str | None = None


@dataclass
class Evidence:
    id: str
    source_candidate_id: str
    observation_id: str
    claim: str
    support: str


@dataclass
class Finding:
    id: str
    claim: str
    evidence_ids: list[str]


@dataclass
class FindingConflict:
    id: str
    left_finding_id: str
    right_finding_id: str


@dataclass
class ResearchRoundResult:
    result: Any | None
    usage: RunUsage
    stop_reason: str


@dataclass
class PipelineWarning:
    stage: str
    error_type: str
    message: str


@dataclass
class PipelineMetrics:
    model_requests: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    elapsed_seconds: float = 0.0
    wall_seconds: float = 0.0

    def add_usage(self, usage: RunUsage) -> None:
        self.model_requests += usage.requests or 0
        self.tool_calls += usage.tool_calls or 0
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0

        if usage.cost is not None:
            self.cost += float(usage.cost)


@dataclass
class AnalysisOutcome:
    """Cross-source analysis state, filled progressively by DEEP.

    Kept as one object so the report can render whatever was already
    validated even when a later enrichment step fails.
    """

    consolidation: "EvidenceConsolidation | None" = None
    findings: list[Finding] | None = None
    finding_conflicts: list[FindingConflict] | None = None
    sufficiency: "ResearchSufficiency | None" = None


class ExtractedEvidence(BaseModel):
    claim: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "A concise factual claim directly supported by the provided source."
        ),
    )

    support: str = Field(
        min_length=1,
        max_length=800,
        description=(
            "A short exact excerpt copied from the provided source content."
        ),
    )


class EvidenceExtraction(BaseModel):
    evidence: list[ExtractedEvidence] = Field(default_factory=list)


class RelevantSourceSelection(BaseModel):
    source_candidate_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Source candidate IDs whose contents may directly "
            "support a useful answer to the research question."
        ),
    )


class EvidenceGroup(BaseModel):
    representative_evidence_id: str = Field(
        description=(
            "One evidence ID whose existing claim best represents this group."
        ),
    )

    evidence_ids: list[str] = Field(
        min_length=1,
        description=(
            "Evidence records that support substantially the same "
            "factual proposition."
        ),
    )


class EvidenceConflict(BaseModel):
    left_group_index: int
    right_group_index: int


class EvidenceConsolidation(BaseModel):
    groups: list[EvidenceGroup] = Field(
        default_factory=list,
    )

    conflicts: list[EvidenceConflict] = Field(
        default_factory=list,
    )


class ResearchGap(BaseModel):
    kind: ResearchGapKind

    description: str = Field(
        min_length=1,
        max_length=500,
        description=(
            "The specific information gap that prevents "
            "the research question from being answered responsibly."
        ),
    )

    related_finding_ids: list[str] = Field(
        default_factory=list,
    )

    related_conflict_ids: list[str] = Field(
        default_factory=list,
    )


class ResearchSufficiency(BaseModel):
    status: ResearchSufficiencyStatus

    supporting_finding_ids: list[str] = Field(
        default_factory=list,
        description=(
            "Existing findings that materially help answer "
            "the research question."
        ),
    )

    unresolved_conflict_ids: list[str] = Field(
        default_factory=list,
    )

    gaps: list[ResearchGap] = Field(
        default_factory=list,
    )


class GroundedStatement(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=800,
    )

    evidence_ids: list[str] = Field(
        min_length=1,
    )


class FinalSynthesis(BaseModel):
    statements: list[GroundedStatement] = Field(
        min_length=1,
        max_length=6,
    )

    limitations: list[str] = Field(
        default_factory=list,
        max_length=4,
    )


@dataclass
class SourceSelectorDeps:
    source_ids: set[str]


@dataclass
class EvidenceExtractorDeps:
    source_text: str


@dataclass
class EvidenceConsolidatorDeps:
    evidence_ids: set[str]


@dataclass
class SufficiencyAssessorDeps:
    finding_ids: set[str]
    conflict_ids: set[str]


@dataclass
class GroundedSynthesisDeps:
    evidence_ids: set[str]
