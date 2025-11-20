# Copyright 2025 Pokee AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import asyncio
import json
import os
from typing import Any, Dict, List
import urllib.parse

import aiohttp
from dotenv import load_dotenv
from pydantic import BaseModel

from logging_utils import setup_colored_logger

load_dotenv()

logger = setup_colored_logger(__name__)


class SearchURLItem(BaseModel):
    """
    A single URL item from search results.

    Attributes:
        url: The full URL of the search result
        title: The title/headline of the webpage
        description: A brief description or snippet from the webpage
    """

    url: str
    title: str
    description: str


class SearchResult(BaseModel):
    """
    Structured results from a search query.

    Attributes:
        query: The original search query
        url_items: List of search result items with URL, title, and description
        raw_response: Original API response (truncated to 500 chars)
        success: Whether the search operation completed successfully
        metadata: Additional metadata about the search (provider, query, etc.)
        error: Error message if the search failed, empty string otherwise
    """

    query: str
    url_items: List[SearchURLItem]
    raw_response: str = ""
    success: bool
    metadata: Dict[str, Any]
    error: str = ""


def _extract_organic_from_serper_response(data: Dict[str, Any]) -> List[SearchURLItem]:
    """
    Extract URLs from Serper API response.

    Args:
        data: The JSON response from Serper API

    Returns:
        List of SearchURLItem objects from organic results
    """
    organic_results = data.get("organic", [])
    return [
        SearchURLItem(
            url=item.get("link", ""),
            title=item.get("title", "No Title"),
            description=item.get("snippet", "No Description"),
        )
        for item in organic_results
    ]


async def serper_search(query: str, timeout: int = 30, top_k: int = 10) -> SearchResult:
    """
    Perform a search using Serper API.

    Args:
        query: The search query string
        timeout: Maximum time in seconds to wait (default: 30)
        top_k: Maximum number of results to return (default: 10)

    Returns:
        SearchResult containing search results and metadata

    Example:
        >>> result = await serper_search("Python programming")
        >>> if result.success:
        ...     for item in result.url_items:
        ...         print(f"{item.title}: {item.url}")
    """
    api_key = os.getenv("SERPER_API_KEY")

    if not api_key:
        return SearchResult(
            query=query,
            url_items=[],
            success=False,
            metadata={
                "provider": "serper",
                "status": 401,
                "execution_time": 0.0,
                "total_results": 0,
            },
            error="SERPER_API_KEY environment variable not found",
        )

    url = "https://google.serper.dev/search"
    payload = {"q": query, "location": "United States", "num": top_k}
    headers = {
        "X-API-KEY": api_key,
        "Content-Type": "application/json",
    }

    loop = asyncio.get_running_loop()
    start_time = loop.time()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                execution_time = loop.time() - start_time

                if response.status == 200:
                    data = await response.json()
                    raw_response = await response.text()
                    url_items = _extract_organic_from_serper_response(data)
                    # Apply boost scoring and stable sort
                    url_items = sorted(
                        enumerate(url_items),
                        key=lambda i_item: (boost_score(i_item[1].url), -i_item[0]),
                        reverse=True
                    )
                    url_items = [item for _, item in url_items]

                    logger.info(
                        f"Search successful for '{query}', found {len(url_items)} results"
                    )

                    return SearchResult(
                        query=query,
                        url_items=url_items,
                        raw_response=raw_response[:500],
                        success=True,
                        metadata={
                            "provider": "serper",
                            "status": 200,
                            "execution_time": execution_time,
                            "total_results": len(url_items),
                            "searchParameters": data.get("searchParameters", {}),
                            "credits": data.get("credits", 0),
                        },
                    )

                else:
                    error_text = await response.text()
                    logger.warning(
                        f"Search failed with HTTP {response.status}: {error_text[:100]}"
                    )
                    return SearchResult(
                        query=query,
                        url_items=[],
                        success=False,
                        metadata={
                            "provider": "serper",
                            "status": response.status,
                            "execution_time": execution_time,
                            "total_results": 0,
                        },
                        error=f"HTTP {response.status}: {error_text[:200]}",
                    )

    except asyncio.TimeoutError:
        logger.warning(f"Search request timed out after {timeout}s")
        return SearchResult(
            query=query,
            url_items=[],
            success=False,
            metadata={
                "provider": "serper",
                "status": 408,
                "execution_time": loop.time() - start_time,
                "total_results": 0,
            },
            error=f"Search request timed out after {timeout}s",
        )

    except aiohttp.ClientError as e:
        logger.warning(f"Client error during search: {str(e)}")
        return SearchResult(
            query=query,
            url_items=[],
            success=False,
            metadata={
                "provider": "serper",
                "status": 502,
                "execution_time": loop.time() - start_time,
                "total_results": 0,
            },
            error=f"Client error: {str(e)[:200]}",
        )

    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON response: {e}")
        return SearchResult(
            query=query,
            url_items=[],
            success=False,
            metadata={
                "provider": "serper",
                "status": 502,
                "execution_time": loop.time() - start_time,
                "total_results": 0,
            },
            error=f"Failed to parse JSON response: {str(e)[:200]}",
        )

    except Exception as e:
        logger.error(f"Unexpected error during search: {str(e)}")
        return SearchResult(
            query=query,
            url_items=[],
            success=False,
            metadata={
                "provider": "serper",
                "status": 500,
                "execution_time": loop.time() - start_time,
                "total_results": 0,
            },
            error=f"Unexpected error: {str(e)[:200]}",
        )


class WebSearchAgent:
    """
    Agent for performing web searches with concurrency control.

    This agent handles search queries with semaphore-based rate limiting
    to prevent overwhelming the search API.
    """

    def __init__(self, config: Dict[str, Any]) -> None:
        """
        Initialize the WebSearchAgent.

        Args:
            config: Configuration dictionary with optional keys:
                - max_concurrent_requests: Max concurrent searches (default: 300)
        """
        self._timeout = config.get("timeout", 30)
        self._top_k = config.get("top_k", 10)
        self._semaphore = asyncio.Semaphore(config.get("max_concurrent_requests", 300))

    async def search(self, query: str) -> SearchResult:
        """
        Perform a web search with concurrency control.

        Args:
            query: Search query string to execute

        Returns:
            SearchResult containing search results and metadata

        Example:
            >>> agent = WebSearchAgent(config={})
            >>> result = await agent.search("Python programming")
            >>> if result.success:
            ...     for item in result.url_items:
            ...         print(item.title)
        """
        logger.info(f"Searching for '{query}'")

        try:
            async with self._semaphore:
                result = await serper_search(
                    query, timeout=self._timeout, top_k=self._top_k
                )

                if not result.success:
                    logger.warning(
                        f"Search failed for '{query}' with status "
                        f"{result.metadata.get('status', 'unknown')}: {result.error}"
                    )

                return result

        except Exception as e:
            logger.error(
                f"Unexpected error in WebSearchAgent for '{query}': {str(e)}",
                exc_info=True,
            )
            return SearchResult(
                query=query,
                url_items=[],
                success=False,
                metadata={
                    "provider": "serper",
                    "status": 500,
                    "execution_time": 0.0,
                    "total_results": 0,
                },
                error=f"WebSearchAgent error: {str(e)[:200]}",
            )
ACADEMIC_WEIGHTS = {
    "wikipedia.org": 100,
    "semanticscholar.org": 98,
    "doi.org": 97,
    "arxiv.org": 95,
    "ncbi.nlm.nih.gov": 95,
    "jstor.org": 94,
    "dblp.org": 93,
    "scholar.google.com": 100,
}

GOV_WEIGHTS = {
    "nih.gov": 95,
    "cdc.gov": 94,
    "nasa.gov": 94,
    "who.int": 93,
    "un.org": 92,
    "europa.eu": 91,
}

TECH_WEIGHTS = {
    "w3.org": 90,
    "ietf.org": 89,
    "readthedocs.io": 89,
    "developer.mozilla.org": 88,
    "docs.python.org": 87,
    "learn.microsoft.com": 86,
    "unicode.org": 85,
    "postgresql.org": 85,
}

OSS_WEIGHTS = {
    "github.com": 84,
    "gitlab.com": 83,
    "apache.org": 82,
    "python.org": 81,
    "rust-lang.org": 81,
    "linuxfoundation.org": 80,
}

COMMUNITY_WEIGHTS = {
    "stackoverflow.com": 84,
    "pypi.org": 83,
    "npmjs.com": 82,
    "crates.io": 81,
}

ALL_WEIGHTS = {
    **ACADEMIC_WEIGHTS,
    **GOV_WEIGHTS,
    **TECH_WEIGHTS,
    **OSS_WEIGHTS,
    **COMMUNITY_WEIGHTS,
}

NEWS_DOMAINS = (
    "reuters.com",
    "apnews.com",
    "pewresearch.org",
    "nytimes.com",
    "theguardian.com",
    "npr.org",
    "pbs.org",
)

TECH_ORG_TOKENS = (
    "kernel",
    "linux",
    "python",
    "apache",
    "mozilla",
    "gnome",
    "kde",
)

def boost_score(url: str) -> int:
    try:
        host = urllib.parse.urlparse(url).netloc.lower().split(":", 1)[0]
        if not host:
            return 0
    except Exception:
        return 0

    for domain, weight in ALL_WEIGHTS.items():
        if host == domain or host.endswith("." + domain):
            return weight

    if host.endswith(".edu") or host.endswith(".gov"):
        return 88
    if host.endswith(".mil") or host.endswith(".int"):
        return 85
    if host.endswith(".ac.uk") or host.endswith(".edu.au"):
        return 87

    for domain in NEWS_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return 72

    if host.endswith(".org") and host.count(".") >= 1:
        if any(token in host for token in TECH_ORG_TOKENS):
            return 65
        return 50

    if host.endswith(".io") and "github" not in host:
        return 55

    return 0
