from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin, urlparse
import logging

import requests
from bs4 import BeautifulSoup

from .config import ResearchConfig
from .utils import clean_text


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class PageEvidence:
    url: str
    title: str = ""
    text: str = ""


class WebResearchClient:
    def __init__(self, config: ResearchConfig | None = None) -> None:
        self.config = config or ResearchConfig()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.config.user_agent})

    def search(self, query: str, max_results: int | None = None) -> list[SearchResult]:
        limit = max_results or self.config.max_search_results
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        try:
            response = self.session.get(url, timeout=self.config.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException:
            logger.warning("Search request failed for query: %s", query)
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        results: list[SearchResult] = []
        for link in soup.select("a.result__a"):
            href = link.get("href", "")
            title = clean_text(link.get_text(" ", strip=True))
            if not href or not title:
                continue
            snippet_node = link.find_parent(class_="result")
            snippet = ""
            if snippet_node:
                snippet = clean_text(" ".join(snippet_node.stripped_strings))
            results.append(SearchResult(title=title, url=href, snippet=snippet))
            if len(results) >= limit:
                break
        return results

    def fetch_page(self, url: str) -> PageEvidence:
        try:
            response = self.session.get(url, timeout=self.config.timeout_seconds, allow_redirects=True)
            response.raise_for_status()
        except requests.RequestException:
            logger.warning("Failed to fetch page: %s", url)
            return PageEvidence(url=url)

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        title = clean_text(soup.title.get_text() if soup.title else "")
        text = clean_text(soup.get_text(" ", strip=True))
        if len(text) > self.config.max_evidence_chars:
            text = text[: self.config.max_evidence_chars]
        return PageEvidence(url=str(response.url), title=title, text=text)

    def discover_official_url(self, app_name: str, homepage_url: str = "") -> str:
        if homepage_url:
            return homepage_url

        results = self.search(f"{app_name} official site", max_results=5)
        for result in results:
            if self._looks_official(result.url, app_name):
                return result.url
        return results[0].url if results else ""

    def discover_evidence_urls(self, app_name: str, homepage_url: str = "") -> list[str]:
        start_url = self.discover_official_url(app_name, homepage_url)
        if not start_url:
            return []

        pages = [start_url]
        homepage = self.fetch_page(start_url)
        pages.extend(self._extract_candidate_links(homepage.url, homepage.text, homepage_url=start_url))

        deduped: list[str] = []
        seen: set[str] = set()
        for page_url in pages:
            normalized = self._normalize_url(page_url)
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped.append(page_url)
        return deduped

    def _extract_candidate_links(self, base_url: str, text: str, homepage_url: str) -> list[str]:
        try:
            response = self.session.get(base_url, timeout=self.config.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException:
            logger.debug("Could not inspect candidate links on: %s", base_url)
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        candidates: list[str] = []
        keywords = ("docs", "api", "developers", "developer", "reference", "auth", "login", "integrations")
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href", "").strip()
            label = clean_text(anchor.get_text(" ", strip=True)).lower()
            if not href:
                continue
            if not any(keyword in href.lower() or keyword in label for keyword in keywords):
                continue
            absolute = urljoin(homepage_url, href)
            candidates.append(absolute)
            if len(candidates) >= self.config.max_pages_per_app - 1:
                break
        return candidates

    def _looks_official(self, url: str, app_name: str) -> bool:
        domain = urlparse(url).netloc.lower()
        name_tokens = [token for token in clean_text(app_name).lower().split() if token]
        return any(token in domain for token in name_tokens)

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
