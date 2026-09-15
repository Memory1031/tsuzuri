import os

import httpx
from pydantic import BaseModel, Field
from trafilatura import extract

TAVILY_SEARCH_URL = "https://api.tavily.com/search"

ANYSEARCH_SEARCH_URL = "https://api.anysearch.com/v1/search"


MAX_WEBPAGE_CHARS = 6_000


class TavilySearchResult(BaseModel):
    title: str
    url: str
    content: str
    score: float


class TavilySearchResponse(BaseModel):
    query: str
    results: list[TavilySearchResult]
    response_time: float | None = None
    request_id: str | None = None


class AnySearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    content: str | None = None


class AnySearchMetadata(BaseModel):
    total_results: int
    search_time_ms: int


class AnySearchData(BaseModel):
    results: list[AnySearchResult]
    metadata: AnySearchMetadata


class AnySearchResponse(BaseModel):
    code: int
    message: str
    request_id: str
    data: AnySearchData


class WebSearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    relevance_score: float | None = None


class WebSearchResponse(BaseModel):
    query: str
    results: list[WebSearchResult]


class WebSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    max_results: int = Field(default=5, ge=1, le=10)


class WebPageResponse(BaseModel):
    url: str
    content: str


def _search_tavily(
    query: str,
    max_results: int,
) -> WebSearchResponse:
    response = httpx.post(
        TAVILY_SEARCH_URL,
        headers={
            "Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}",
        },
        json={
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_answer": False,
            "include_raw_content": False,
        },
        timeout=10,
    )

    response.raise_for_status()

    tavily_response = TavilySearchResponse.model_validate(response.json())

    return WebSearchResponse(
        query=tavily_response.query,
        results=[
            WebSearchResult(
                title=result.title,
                url=result.url,
                snippet=result.content,
                relevance_score=result.score,
            )
            for result in tavily_response.results
        ],
    )


def _search_anysearch(
    query: str,
    max_results: int,
) -> WebSearchResponse:
    response = httpx.post(
        ANYSEARCH_SEARCH_URL,
        headers={
            "Authorization": (f"Bearer {os.environ['ANYSEARCH_API_KEY']}"),
        },
        json={
            "query": query,
            "max_results": max_results,
            "format": "json",
        },
        timeout=10,
    )

    response.raise_for_status()

    anysearch_response = AnySearchResponse.model_validate(response.json())

    if anysearch_response.code != 0:
        raise RuntimeError(f"AnySearch failed: {anysearch_response.message}")

    return WebSearchResponse(
        query=query,
        results=[
            WebSearchResult(
                title=result.title,
                url=result.url,
                snippet=result.snippet,
                relevance_score=None,
            )
            for result in anysearch_response.data.results
        ],
    )


def web_search(
    query: str,
    max_results: int = 5,
) -> WebSearchResponse:
    """Search the public web and return normalized search results.

    Args:
        query: Search query.
        max_results: Maximum number of results to return.
    """
    request = WebSearchRequest(
        query=query,
        max_results=max_results,
    )

    provider = os.getenv(
        "WEB_SEARCH_PROVIDER",
        "tavily",
    ).lower()

    if provider == "tavily":
        return _search_tavily(
            request.query,
            request.max_results,
        )

    if provider == "anysearch":
        return _search_anysearch(
            request.query,
            request.max_results,
        )

    raise ValueError(f"Unsupported web search provider: {provider}")


def read_webpage(url: str) -> WebPageResponse:
    """Fetch a webpage and extract its readable main content.

    Args:
        url: URL of the webpage to read.

    Raises:
        httpx.HTTPStatusError:
            If the HTTP request returns an unsuccessful status code.
        RuntimeError:
            If readable page content cannot be extracted.
    """
    response = httpx.get(
        url,
        follow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
        },
        timeout=15,
    )

    response.raise_for_status()

    content = extract(
        response.text,
        include_links=True,
        include_formatting=False,
    )

    if not content:
        raise RuntimeError("Unable to extract readable content from webpage")

    return WebPageResponse(
        url=str(response.url),
        content=content[:MAX_WEBPAGE_CHARS],
    )
