from __future__ import annotations

from pathlib import Path
import json
import logging
import os
import re
from typing import Any

from openai import OpenAI

from .config import AppRecord, INPUT_CSV, RESULTS_CSV, ResearchConfig
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .search import PageEvidence, WebResearchClient
from .utils import append_csv_record, coalesce, clean_text, read_csv_records, safe_join, write_csv_records


logger = logging.getLogger(__name__)

API_KEYWORDS = {
    "REST": ("rest api", "restful", "endpoint", "/v1", "/api/"),
    "GraphQL": ("graphql", "graphql api", "query {", "mutation {"),
    "SOAP": ("soap", "wsdl"),
    "gRPC": ("grpc",),
}

AUTH_KEYWORDS = {
    "API key": ("api key", "personal access token", "pat"),
    "OAuth": ("oauth", "oauth2", "oauth 2"),
    "Bearer token": ("bearer token", "authorization: bearer"),
    "SSO": ("sso", "saml", "openid connect"),
}

GATING_SIGNALS = (
    "request demo",
    "contact sales",
    "book a demo",
    "talk to sales",
    "enterprise only",
    "waitlist",
)

CATEGORY_KEYWORDS = {
    "CRM": "crm",
    "Analytics": "analytics",
    "Marketing": "marketing",
    "Customer Support": "support",
    "Finance": "finance",
    "HR": "hr",
    "Project Management": "project management",
    "Communication": "communication",
    "Developer Tools": "developer",
}


class ResearchAgent:
    def __init__(self, config: ResearchConfig | None = None, web_client: WebResearchClient | None = None) -> None:
        self.config = config or ResearchConfig()
        self.web = web_client or WebResearchClient(self.config)
        self.openai_client = OpenAI() if self._has_openai_key() else None

    def run(self, input_csv: Path = INPUT_CSV, output_csv: Path = RESULTS_CSV) -> list[dict[str, str]]:
        """Process every app in the input file one at a time and return JSON-ready results."""
        apps = self.load_apps(input_csv)
        logger.info("Loaded %s apps from %s", len(apps), input_csv)

        write_csv_records(output_csv, [])
        results: list[dict[str, str]] = []

        for index, app in enumerate(apps, start=1):
            logger.info("Processing app %s/%s: %s", index, len(apps), app.app_name)
            try:
                result = self.research_app(app)
            except Exception as exc:  # noqa: BLE001 - we want app-level resilience here
                logger.exception("Failed while researching %s", app.app_name)
                result = self._build_error_result(app, str(exc))
            results.append(result)
            append_csv_record(output_csv, result)

        logger.info("Finished research for %s apps. Output saved to %s", len(results), output_csv)
        return results

    def load_apps(self, path: Path) -> list[AppRecord]:
        """Read apps.csv and turn each row into a strongly typed app record."""
        records = read_csv_records(path)
        apps: list[AppRecord] = []
        for record in records:
            app_name = clean_text(record.get("app_name") or record.get("App Name"))
            if not app_name:
                continue
            apps.append(
                AppRecord(
                    app_name=app_name,
                    category=clean_text(record.get("category") or record.get("Category")),
                    homepage_url=clean_text(record.get("homepage_url") or record.get("Homepage URL") or record.get("url")),
                )
            )
        return apps

    def research_app(self, app: AppRecord) -> dict[str, str]:
        """Research a single app and return one JSON object with the required fields."""
        official_url = self.web.discover_official_url(app.app_name, app.homepage_url)
        evidence_pages = self.collect_evidence(app.app_name, official_url)
        evidence_text = self.render_evidence(evidence_pages)

        ai_result = self._run_openai_analysis(app, official_url, evidence_text)
        if ai_result:
            logger.debug("Using OpenAI result for %s", app.app_name)
            # attempt to enrich any Unknown fields from developer docs
            ai_result = self._augment_from_developer_docs(app, ai_result, evidence_pages)
            return self._format_result(ai_result)

        logger.debug("Using heuristic fallback for %s", app.app_name)
        heuristic_result = self._heuristic_analysis(app, official_url, evidence_pages)
        heuristic_result = self._augment_from_developer_docs(app, heuristic_result, evidence_pages)
        return self._format_result(heuristic_result)

    def _augment_from_developer_docs(self, app: AppRecord, payload: dict[str, Any], pages: list[PageEvidence]) -> dict[str, Any]:
        """If key fields are Unknown, perform extra searches against developer/docs/auth pages and re-run lightweight heuristics.

        This function looks for developer docs, API reference, auth guides, OAuth/GraphQL docs and uses them to fill
        missing values for `authentication_method`, `api_type`, `buildability_verdict`, and `existing_mcp`.
        """
        needs_auth = payload.get("authentication_method", "Unknown") == "Unknown"
        needs_api = payload.get("api_type", "Unknown") == "Unknown"
        needs_build = payload.get("buildability_verdict", "Unknown") == "Unknown"
        needs_mcp = payload.get("existing_mcp", "Unknown") == "Unknown"

        if not (needs_auth or needs_api or needs_build or needs_mcp):
            return payload

        # Build targeted queries to find developer-centric pages
        queries = [
            f"{app.app_name} developer docs",
            f"{app.app_name} api documentation",
            f"{app.app_name} authentication",
            f"{app.app_name} oauth",
            f"{app.app_name} graphql",
        ]

        dev_urls: list[str] = []
        for q in queries:
            try:
                results = self.web.search(q, max_results=5)
            except Exception:
                results = []
            for r in results:
                # prefer urls with docs/api/auth keywords
                if any(k in r.url.lower() for k in ("/docs", "/api", "/developers", "swagger", "openapi", "/auth", "/graphql")):
                    dev_urls.append(r.url)
            if len(dev_urls) >= self.config.max_pages_per_app:
                break

        # fetch dev doc pages and combine text
        dev_pages = [self.web.fetch_page(u) for u in dev_urls[: self.config.max_pages_per_app]]
        combined_dev_text = " ".join((p.text or "") for p in dev_pages).lower()

        # only attempt to fill fields if we found developer docs
        if combined_dev_text.strip():
            if needs_api:
                payload["api_type"] = coalesce(self._detect_keyword_bucket(combined_dev_text, API_KEYWORDS), payload.get("api_type", "Unknown"))
            if needs_auth:
                payload["authentication_method"] = coalesce(self._detect_auth_method(combined_dev_text), payload.get("authentication_method", "Unknown"))
            # gated vs self-serve may be clearer in auth/docs pages
            self_serve_or_gated = "Gated" if self._is_gated(combined_dev_text) else payload.get("self_serve_or_gated")
            payload["self_serve_or_gated"] = coalesce(self_serve_or_gated, payload.get("self_serve_or_gated"))
            if needs_mcp:
                payload["existing_mcp"] = "Yes" if ("model context protocol" in combined_dev_text or re.search(r"\bmcp\b", combined_dev_text)) else payload.get("existing_mcp", "Unknown")
            if needs_build:
                api_type = payload.get("api_type", "Unknown")
                auth_method = payload.get("authentication_method", "Unknown")
                payload["buildability_verdict"], payload["main_blocker"] = self._estimate_buildability(combined_dev_text, api_type, auth_method, payload.get("self_serve_or_gated", "Self Serve"))

            # prefer the first dev doc URL as stronger evidence
            if dev_pages and dev_pages[0].url:
                payload["evidence_url"] = payload.get("evidence_url") or dev_pages[0].url

        return payload

    def collect_evidence(self, app_name: str, official_url: str) -> list[PageEvidence]:
        """Discover likely documentation pages and fetch the text we need for classification."""
        urls = self.web.discover_evidence_urls(app_name, official_url)
        limited_urls = urls[: self.config.max_pages_per_app]
        evidence_pages = [self.web.fetch_page(url) for url in limited_urls if url]
        return [page for page in evidence_pages if page.text or page.title]

    def render_evidence(self, pages: list[PageEvidence]) -> str:
        """Flatten page evidence into one prompt-safe text block."""
        sections: list[str] = []
        for page in pages:
            sections.append(f"URL: {page.url}\nTitle: {page.title}\nText: {page.text}")
        return "\n\n".join(sections)[: self.config.max_evidence_chars]

    def _run_openai_analysis(self, app: AppRecord, official_url: str, evidence_text: str) -> dict[str, str] | None:
        """Ask OpenAI to classify the app from collected evidence and normalize the JSON response."""
        if not self.openai_client:
            return None

        prompt = USER_PROMPT_TEMPLATE.format(
            app_name=app.app_name,
            category=app.category,
            official_url=official_url,
            evidence=evidence_text,
        )
        try:
            response = self.openai_client.responses.create(
                model=self.config.openai_model,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
        except Exception as exc:  # noqa: BLE001 - downstream fallback handles the failure
            logger.warning("OpenAI request failed for %s: %s", app.app_name, exc)
            return None

        text = self._extract_response_text(response)
        if not text:
            return None

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = self._extract_json_object(text)
        if not isinstance(parsed, dict):
            return None
        return self._normalize_ai_payload(app, official_url, parsed)

    def _heuristic_analysis(self, app: AppRecord, official_url: str, pages: list[PageEvidence]) -> dict[str, str]:
        """Classify the app using deterministic rules when the LLM is unavailable or fails."""
        combined_text = " ".join(page.text.lower() for page in pages)
        combined_text += f" {app.app_name.lower()} {app.category.lower()}"

        api_type = self._detect_keyword_bucket(combined_text, API_KEYWORDS)
        auth_method = self._detect_auth_method(combined_text)
        self_serve_or_gated = "Gated" if self._is_gated(combined_text) else "Self Serve"
        existing_mcp = "Yes" if "model context protocol" in combined_text or re.search(r"\bmcp\b", combined_text) else "No"
        api_surface = self._estimate_api_surface(combined_text)
        buildability_verdict, main_blocker = self._estimate_buildability(combined_text, api_type, auth_method, self_serve_or_gated)

        description = self._extract_description(pages, app)
        category = coalesce(app.category, self._guess_category_from_text(combined_text))

        return {
            "app_name": app.app_name,
            "category": category,
            "one_line_description": description,
            "authentication_method": auth_method,
            "self_serve_or_gated": self_serve_or_gated,
            "api_type": api_type,
            "api_surface": api_surface,
            "existing_mcp": existing_mcp,
            "buildability_verdict": buildability_verdict,
            "main_blocker": main_blocker,
            "evidence_url": official_url or (pages[0].url if pages else ""),
        }

    def _format_result(self, payload: dict[str, Any]) -> dict[str, str]:
        """Convert the internal result shape into the CSV/JSON schema the assignment expects."""
        return {
            "App Name": clean_text(payload.get("app_name")),
            "Category": clean_text(payload.get("category")),
            "One-line description": clean_text(payload.get("one_line_description")),
            "Authentication method": clean_text(payload.get("authentication_method")),
            "Self Serve or Gated": clean_text(payload.get("self_serve_or_gated")),
            "API Type": clean_text(payload.get("api_type")),
            "API Surface": clean_text(payload.get("api_surface")),
            "Existing MCP": clean_text(payload.get("existing_mcp")),
            "Buildability Verdict": clean_text(payload.get("buildability_verdict")),
            "Main Blocker": clean_text(payload.get("main_blocker")),
            "Evidence URL": clean_text(payload.get("evidence_url")),
        }

    def _normalize_ai_payload(self, app: AppRecord, official_url: str, payload: dict[str, Any]) -> dict[str, str]:
        """Fill missing model fields with safe defaults and keep the output shape stable."""
        return {
            "app_name": coalesce(payload.get("app_name"), app.app_name),
            "category": coalesce(payload.get("category"), app.category),
            "one_line_description": coalesce(payload.get("one_line_description"), "Unknown"),
            "authentication_method": coalesce(payload.get("authentication_method"), "Unknown"),
            "self_serve_or_gated": coalesce(payload.get("self_serve_or_gated"), "Unknown"),
            "api_type": coalesce(payload.get("api_type"), "Unknown"),
            "api_surface": coalesce(payload.get("api_surface"), "Unknown"),
            "existing_mcp": coalesce(payload.get("existing_mcp"), "Unknown"),
            "buildability_verdict": coalesce(payload.get("buildability_verdict"), "Unknown"),
            "main_blocker": coalesce(payload.get("main_blocker"), ""),
            "evidence_url": coalesce(payload.get("evidence_url"), official_url),
        }

    def _detect_keyword_bucket(self, text: str, buckets: dict[str, tuple[str, ...]]) -> str:
        """Find the first API keyword bucket that appears in the evidence text."""
        for label, keywords in buckets.items():
            if any(keyword in text for keyword in keywords):
                return label
        return "Unknown"

    def _detect_auth_method(self, text: str) -> str:
        """Detect the most likely authentication method from the collected evidence."""
        matches = []
        for label, keywords in AUTH_KEYWORDS.items():
            if any(keyword in text for keyword in keywords):
                matches.append(label)
        if not matches:
            return "Unknown"
        return safe_join(sorted(set(matches)), ", ")

    def _is_gated(self, text: str) -> bool:
        """Determine whether the product appears to require a sales/demo flow."""
        return any(signal in text for signal in GATING_SIGNALS)

    def _estimate_api_surface(self, text: str) -> str:
        """Estimate API size based on how much public integration surface is described."""
        signals = {
            "Large": ("graphql", "webhook", "sdk", "api reference", "rate limit", "many endpoints"),
            "Medium": ("api key", "integration", "endpoint", "oauth"),
            "Small": ("single endpoint", "limited api", "few endpoints"),
        }
        for label, keywords in signals.items():
            if any(keyword in text for keyword in keywords):
                return label
        return "Unknown"

    def _estimate_buildability(self, text: str, api_type: str, auth_method: str, self_serve_or_gated: str) -> tuple[str, str]:
        """Turn the evidence into a pragmatic buildability verdict and short blocker note."""
        if self_serve_or_gated == "Gated":
            return "Partially buildable", "Access appears gated behind a sales or demo flow."
        if api_type == "Unknown":
            return "Blocked", "No public API or API documentation was found in the collected evidence."
        if auth_method == "Unknown":
            return "Partially buildable", "Authentication requirements are not clearly documented."
        return "Buildable", "No obvious blocker found in the collected evidence."

    def _extract_description(self, pages: list[PageEvidence], app: AppRecord) -> str:
        """Pull a short factual description from the first useful evidence sentence.

        Uses `clean_description` to remove navigation noise and return a single clean sentence.
        """
        from .utils import clean_description

        for page in pages:
            sentences = re.split(r"(?<=[.!?])\s+", page.text)
            for sentence in sentences:
                candidate = clean_description(sentence, app.app_name)
                # clean_description returns a fallback when too short; prefer descriptive ones
                if candidate and len(candidate) >= 40 and "skip to content" not in candidate.lower():
                    return candidate[:240]
        # If no descriptive sentence found, attempt to form one from the page title
        if pages:
            title_candidate = clean_description(pages[0].title, app.app_name)
            if title_candidate:
                return title_candidate
        if app.category:
            return f"{app.app_name} is a {app.category.lower()} product."
        return f"{app.app_name} is a SaaS product."

    def _guess_category_from_text(self, text: str) -> str:
        """Guess a category from common product-language keywords."""
        for category, keyword in CATEGORY_KEYWORDS.items():
            if keyword in text:
                return category
        return "Unknown"

    def _extract_response_text(self, response: Any) -> str:
        """Extract text from the OpenAI Responses API object without assuming a single response shape."""
        if hasattr(response, "output_text"):
            return clean_text(getattr(response, "output_text"))
        output = getattr(response, "output", None)
        if not output:
            return ""
        parts: list[str] = []
        for item in output:
            content = getattr(item, "content", None)
            if not content:
                continue
            for block in content:
                text = getattr(block, "text", "")
                if text:
                    parts.append(text)
        return clean_text("\n".join(parts))

    def _extract_json_object(self, text: str) -> dict[str, Any] | None:
        """Recover a JSON object if the model wrapped it in extra prose."""
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    def _build_error_result(self, app: AppRecord, error_message: str) -> dict[str, str]:
        """Create a safe fallback row when a single app fails so the batch can continue."""
        logger.error("Using fallback result for %s", app.app_name)
        return {
            "App Name": app.app_name,
            "Category": app.category or "Unknown",
            "One-line description": "Unknown",
            "Authentication method": "Unknown",
            "Self Serve or Gated": "Unknown",
            "API Type": "Unknown",
            "API Surface": "Unknown",
            "Existing MCP": "Unknown",
            "Buildability Verdict": "Blocked",
            "Main Blocker": clean_text(error_message) or "Unexpected error during research.",
            "Evidence URL": app.homepage_url,
        }

    def _has_openai_key(self) -> bool:
        """Check whether the OpenAI client can be enabled."""
        return bool(os.getenv("OPENAI_API_KEY"))


def run_research() -> list[dict[str, str]]:
    """Convenience entry point for programmatic callers."""
    agent = ResearchAgent()
    return agent.run()
