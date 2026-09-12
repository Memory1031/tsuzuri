import os

import httpx
from pydantic import BaseModel, Field
from trafilatura import extract

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
MAX_WEBPAGE_CHARS = 15_000


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


def web_search(query: str, max_results: int = 5) -> WebSearchResponse:
    """Search the public web and return normalized search results.

    Args:
        query: Search query.
        max_results: Maximum number of results to return.
    """
    request = WebSearchRequest(
        query=query,
        max_results=max_results,
    )

    response = httpx.post(
        TAVILY_SEARCH_URL,
        headers={
            "Authorization": f"Bearer {os.getenv('WEB_SEARCH_API_KEY')}",
        },
        json={
            "query": request.query,
            "search_depth": "basic",
            "max_results": request.max_results,
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
