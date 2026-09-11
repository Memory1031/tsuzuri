from typing import Any

import httpx
from pydantic import BaseModel, Field

VNDB_API_URL = "https://api.vndb.org/kana/vn"


class VndbSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    results: int = Field(default=5, ge=1, le=100)

    def to_payload(self) -> dict[str, Any]:
        return {
            "filters": ["search", "=", self.query],
            "fields": "title,alttitle,released,rating,votecount",
            "sort": "searchrank",
            "results": self.results,
        }


class VndbVisualNovel(BaseModel):
    id: str
    title: str
    alttitle: str | None
    released: str | None
    rating: float | None
    votecount: int


class VndbSearchResponse(BaseModel):
    more: bool
    results: list[VndbVisualNovel]


class VndbDeveloper(BaseModel):
    id: str
    name: str
    original: str | None


class VndbVisualNovelDetail(BaseModel):
    id: str
    title: str
    alttitle: str | None
    released: str | None
    rating: float | None
    votecount: int
    description: str | None
    developers: list[VndbDeveloper]


def search_vndb(query: str, results: int = 5) -> VndbSearchResponse:
    request = VndbSearchRequest(query=query, results=results)

    response = httpx.post(
        VNDB_API_URL,
        json=request.to_payload(),
        timeout=10,
    )

    response.raise_for_status()

    return VndbSearchResponse.model_validate(response.json())

def get_vndb(vn_id: str) -> VndbVisualNovelDetail | None:
    response = httpx.post(
        VNDB_API_URL,
        json={
            "filters": ["id", "=", vn_id],
            "fields": "title,alttitle,released,rating,votecount,description,developers{name,original}",
            "results": 1,
        },
        timeout=10,
    )

    response.raise_for_status()

    data = response.json()

    if not data["results"]:
        return None

    return VndbVisualNovelDetail.model_validate(data["results"][0])
