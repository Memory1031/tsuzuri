"""Runtime-owned research state.

The state records what actually happened during a run. IDs are
generated here; models never supply provenance.
"""

from dataclasses import dataclass, field
from threading import Lock

from tsuzuri.research.models import (
    Evidence,
    Observation,
    ObservationStatus,
    SourceCandidate,
    SourceKind,
)


@dataclass
class ResearchState:
    observations: list[Observation] = field(default_factory=list)
    sources: list[SourceCandidate] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)

    processed_source_ids: set[str] = field(default_factory=set)

    _next_observation_id: int = field(
        default=1,
        init=False,
        repr=False,
    )

    _next_source_id: int = field(
        default=1,
        init=False,
        repr=False,
    )

    _next_evidence_id: int = field(
        default=1,
        init=False,
        repr=False,
    )

    _lock: Lock = field(
        default_factory=Lock,
        init=False,
        repr=False,
    )

    def record_observation(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        status: ObservationStatus,
        result: object | None = None,
        error: str | None = None,
    ) -> Observation:
        with self._lock:
            observation = Observation(
                id=f"obs_{self._next_observation_id}",
                tool_name=tool_name,
                arguments=arguments,
                status=status,
                result=result,
                error=error,
            )

            self._next_observation_id += 1
            self.observations.append(observation)

        return observation

    def record_source(
        self,
        *,
        kind: SourceKind,
        observation_id: str,
        source_name: str,
        source_url: str | None = None,
        source_id: str | None = None,
        content: object | None = None,
    ) -> SourceCandidate:
        with self._lock:
            source = SourceCandidate(
                id=f"src_{self._next_source_id}",
                kind=kind,
                observation_id=observation_id,
                source_name=source_name,
                source_url=source_url,
                source_id=source_id,
                content=content,
            )

            self._next_source_id += 1
            self.sources.append(source)

        return source

    def record_evidence(
        self,
        *,
        source: SourceCandidate,
        claim: str,
        support: str,
    ) -> Evidence:
        with self._lock:
            evidence = Evidence(
                id=f"ev_{self._next_evidence_id}",
                source_candidate_id=source.id,
                observation_id=source.observation_id,
                claim=claim,
                support=support,
            )

            self._next_evidence_id += 1
            self.evidence.append(evidence)

        return evidence
