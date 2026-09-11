import os

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class WebSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    max_results: int = Field(default=5, ge=1, le=20)

    def to_payload(self) -> dict:
        return {
            "query": self.query,
            "search_depth": "basic",
            "max_results": self.max_results,
            "include_answer": False,
            "include_raw_content": False,
        }


request = WebSearchRequest(
    query="STEINS;GATE Steam release date",
    max_results=5,
)

response = httpx.post(
    TAVILY_SEARCH_URL,
    headers={
        "Authorization": f"Bearer {os.getenv('WEB_SEARCH_API_KEY')}",
        "Content-Type": "application/json",
    },
    json=request.to_payload(),
    timeout=10,
)

print("status:", response.status_code)
print(response.text)