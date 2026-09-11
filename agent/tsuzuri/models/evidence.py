from enum import StrEnum

from pydantic import BaseModel, Field


class EvidenceKind(StrEnum):
    STRUCTURED_API = "structured_api"
    WEB_SEARCH_SNIPPET = "web_search_snippet"
    WEB_PAGE = "web_page"


class EvidenceSource(BaseModel):
    ref: str

    kind: EvidenceKind

    source_name: str = Field(min_length=1)

    source_id: str | None = None

    source_url: str | None = None

    support: str = Field(min_length=1)

    observation_id: str


class Evidence(BaseModel):
    id: str

    kind: EvidenceKind

    source_name: str = Field(min_length=1)

    source_id: str | None = None

    source_url: str | None = None

    claim: str = Field(min_length=1)

    support: str = Field(min_length=1)

    source_observation_id: str
