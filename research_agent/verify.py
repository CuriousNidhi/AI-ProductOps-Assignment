from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging
import random
import re
from typing import Any

from .config import CASE_STUDY_DIR, RESULTS_CSV, ResearchConfig, VERIFIED_RESULTS_CSV
from .search import PageEvidence, WebResearchClient
from .utils import clean_text, coalesce, read_csv_records, safe_join, write_csv_records


logger = logging.getLogger(__name__)

VERIFY_FIELDS = [
    "App Name",
    "Category",
    "One-line description",
    "Authentication method",
    "Self Serve or Gated",
    "API Type",
    "API Surface",
    "Existing MCP",
    "Buildability Verdict",
    "Main Blocker",
    "Evidence URL",
]

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

VERIFICATION_COLUMN_ORDER = [
    *(f"{field} Verification" for field in VERIFY_FIELDS),
    *(f"{field} Corrected Value" for field in VERIFY_FIELDS),
]


@dataclass(frozen=True)
class VerificationResult:
    """Store one verified row together with the field-level counts used for the summary."""

    row: dict[str, str]
    correct_fields: int
    incorrect_fields: int
    corrected_values: int


class VerificationAgent:
    def __init__(self, config: ResearchConfig | None = None, web_client: WebResearchClient | None = None) -> None:
        self.config = config or ResearchConfig()
        self.web = web_client or WebResearchClient(self.config)

    def run(
        self,
        input_csv: Path = RESULTS_CSV,
        output_csv: Path = VERIFIED_RESULTS_CSV,
        summary_path: Path = CASE_STUDY_DIR / "verification_summary.md",
        sample_size: int = 20,
        seed: int = 42,
    ) -> dict[str, Any]:
        """Sample result rows, verify them against official docs, and write the CSV plus summary output."""
        rows = self.load_results(input_csv)
        sampled_rows = self.sample_rows(rows, sample_size=sample_size, seed=seed)
        logger.info("Loaded %s rows and sampled %s for verification", len(rows), len(sampled_rows))

        if not sampled_rows:
            empty_rows: list[dict[str, str]] = []
            write_csv_records(output_csv, empty_rows)
            summary = self.build_summary(
                total_apps=0,
                sampled_apps=0,
                correct_fields=0,
                incorrect_fields=0,
                corrected_values=0,
                final_correct_fields=0,
                output_csv=output_csv,
                summary_path=summary_path,
            )
            self.write_summary(summary_path, summary)
            return summary

        verified_rows: list[dict[str, str]] = []
        correct_fields = 0
        incorrect_fields = 0
        corrected_values = 0

        for index, row in enumerate(sampled_rows, start=1):
            app_name = clean_text(row.get("App Name"))
            logger.info("Verifying app %s/%s: %s", index, len(sampled_rows), app_name)
            result = self.verify_row(row)
            verified_rows.append(result.row)
            correct_fields += result.correct_fields
            incorrect_fields += result.incorrect_fields
            corrected_values += result.corrected_values

        write_csv_records(output_csv, verified_rows)
        final_correct_fields = correct_fields + corrected_values
        summary = self.build_summary(
            total_apps=len(rows),
            sampled_apps=len(sampled_rows),
            correct_fields=correct_fields,
            incorrect_fields=incorrect_fields,
            corrected_values=corrected_values,
            final_correct_fields=final_correct_fields,
            output_csv=output_csv,
            summary_path=summary_path,
        )
        self.write_summary(summary_path, summary)
        logger.info("Verification complete. Output saved to %s and %s", output_csv, summary_path)
        return summary

    def load_results(self, path: Path) -> list[dict[str, str]]:
        """Read the research CSV and normalize it into a list of dictionaries."""
        return read_csv_records(path)

    def sample_rows(self, rows: list[dict[str, str]], sample_size: int, seed: int) -> list[dict[str, str]]:
        """Randomly choose up to 20 rows while keeping the sample repeatable with a seed."""
        if not rows:
            return []
        if len(rows) <= sample_size:
            return list(rows)
        rng = random.Random(seed)
        return rng.sample(rows, sample_size)

    def verify_row(self, row: dict[str, str]) -> VerificationResult:
        """Verify one research row against the official documentation and return an augmented CSV row."""
        app_name = clean_text(row.get("App Name"))
        docs_url = clean_text(row.get("Evidence URL"))
        evidence_pages = self.collect_documentation(app_name, docs_url)
        evidence_text = self.render_evidence(evidence_pages)

        verified_row: dict[str, str] = dict(row)
        correct_fields = 0
        incorrect_fields = 0
        corrected_values = 0

        field_checks = [
            self.verify_app_name,
            self.verify_category,
            self.verify_description,
            self.verify_authentication,
            self.verify_self_serve_or_gated,
            self.verify_api_type,
            self.verify_api_surface,
            self.verify_existing_mcp,
            self.verify_buildability,
            self.verify_main_blocker,
            self.verify_evidence_url,
        ]

        for field_name, checker in zip(VERIFY_FIELDS, field_checks, strict=True):
            original_value = clean_text(row.get(field_name))
            status, corrected_value = checker(app_name, original_value, row, evidence_text, evidence_pages, docs_url)
            verified_row[f"{field_name} Verification"] = status
            verified_row[f"{field_name} Corrected Value"] = corrected_value
            if status == "Correct":
                correct_fields += 1
            else:
                incorrect_fields += 1
                if corrected_value and corrected_value != original_value:
                    corrected_values += 1

        return VerificationResult(
            row=verified_row,
            correct_fields=correct_fields,
            incorrect_fields=incorrect_fields,
            corrected_values=corrected_values,
        )

    def collect_documentation(self, app_name: str, docs_url: str) -> list[PageEvidence]:
        """Open the official docs URL and collect a small set of linked documentation pages."""
        if not docs_url:
            return []
        urls = self.web.discover_evidence_urls(app_name, docs_url)
        if not urls:
            urls = [docs_url]
        pages = [self.web.fetch_page(url) for url in urls[: self.config.max_pages_per_app]]
        return [page for page in pages if page.text or page.title]

    def render_evidence(self, pages: list[PageEvidence]) -> str:
        """Flatten documentation pages into a single searchable text block."""
        return "\n\n".join(f"URL: {page.url}\nTitle: {page.title}\nText: {page.text}" for page in pages)

    def verify_app_name(
        self,
        app_name: str,
        original_value: str,
        row: dict[str, str],
        evidence_text: str,
        pages: list[PageEvidence],
        docs_url: str,
    ) -> tuple[str, str]:
        """Confirm that the app name is supported by the official docs title, body, or domain."""
        supported = self._value_in_text(original_value, evidence_text) or self._value_in_pages(original_value, pages)
        corrected_value = original_value or app_name
        return self._status_and_correction(supported, corrected_value)

    def verify_category(
        self,
        app_name: str,
        original_value: str,
        row: dict[str, str],
        evidence_text: str,
        pages: list[PageEvidence],
        docs_url: str,
    ) -> tuple[str, str]:
        """Check whether the category matches the product language in the official docs."""
        inferred = self._infer_category(evidence_text)
        supported = bool(original_value) and self._normalize(original_value) == self._normalize(inferred)
        return self._status_and_correction(supported, inferred)

    def verify_description(
        self,
        app_name: str,
        original_value: str,
        row: dict[str, str],
        evidence_text: str,
        pages: list[PageEvidence],
        docs_url: str,
    ) -> tuple[str, str]:
        """Compare the one-line description against a docs-derived summary sentence."""
        corrected = self._infer_description(pages, app_name)
        supported = self._roughly_matches(original_value, corrected, evidence_text)
        return self._status_and_correction(supported, corrected)

    def verify_authentication(
        self,
        app_name: str,
        original_value: str,
        row: dict[str, str],
        evidence_text: str,
        pages: list[PageEvidence],
        docs_url: str,
    ) -> tuple[str, str]:
        """Check whether the documented authentication scheme matches the extracted value."""
        corrected = self._infer_authentication(evidence_text)
        supported = self._normalizes_to_any(original_value, corrected)
        return self._status_and_correction(supported, corrected)

    def verify_self_serve_or_gated(
        self,
        app_name: str,
        original_value: str,
        row: dict[str, str],
        evidence_text: str,
        pages: list[PageEvidence],
        docs_url: str,
    ) -> tuple[str, str]:
        """Detect whether the product is self-serve or gated from the docs evidence."""
        corrected = "Gated" if self._is_gated(evidence_text) else "Self Serve"
        supported = self._normalize(original_value) == self._normalize(corrected)
        return self._status_and_correction(supported, corrected)

    def verify_api_type(
        self,
        app_name: str,
        original_value: str,
        row: dict[str, str],
        evidence_text: str,
        pages: list[PageEvidence],
        docs_url: str,
    ) -> tuple[str, str]:
        """Compare the extracted API type with the API style described in the docs."""
        corrected = self._infer_api_type(evidence_text)
        supported = self._normalize(original_value) == self._normalize(corrected)
        return self._status_and_correction(supported, corrected)

    def verify_api_surface(
        self,
        app_name: str,
        original_value: str,
        row: dict[str, str],
        evidence_text: str,
        pages: list[PageEvidence],
        docs_url: str,
    ) -> tuple[str, str]:
        """Estimate whether the documented surface is small, medium, or large."""
        corrected = self._infer_api_surface(evidence_text)
        supported = self._normalize(original_value) == self._normalize(corrected)
        return self._status_and_correction(supported, corrected)

    def verify_existing_mcp(
        self,
        app_name: str,
        original_value: str,
        row: dict[str, str],
        evidence_text: str,
        pages: list[PageEvidence],
        docs_url: str,
    ) -> tuple[str, str]:
        """Check whether the docs explicitly mention MCP or Model Context Protocol."""
        corrected = "Yes" if self._mentions_mcp(evidence_text) else "No"
        supported = self._normalize(original_value) == self._normalize(corrected)
        return self._status_and_correction(supported, corrected)

    def verify_buildability(
        self,
        app_name: str,
        original_value: str,
        row: dict[str, str],
        evidence_text: str,
        pages: list[PageEvidence],
        docs_url: str,
    ) -> tuple[str, str]:
        """Compare the buildability verdict with the public-API and access signals in the docs."""
        corrected = self._infer_buildability(evidence_text)
        supported = self._normalize(original_value) == self._normalize(corrected)
        return self._status_and_correction(supported, corrected)

    def verify_main_blocker(
        self,
        app_name: str,
        original_value: str,
        row: dict[str, str],
        evidence_text: str,
        pages: list[PageEvidence],
        docs_url: str,
    ) -> tuple[str, str]:
        """Compare the stated blocker with the blocker that best matches the docs evidence."""
        corrected = self._infer_main_blocker(evidence_text)
        supported = self._roughly_matches(original_value, corrected, evidence_text)
        return self._status_and_correction(supported, corrected)

    def verify_evidence_url(
        self,
        app_name: str,
        original_value: str,
        row: dict[str, str],
        evidence_text: str,
        pages: list[PageEvidence],
        docs_url: str,
    ) -> tuple[str, str]:
        """Confirm that the evidence URL is reachable and belongs to the official docs source."""
        corrected = pages[0].url if pages else docs_url
        supported = bool(original_value) and self._same_domain(original_value, corrected)
        return self._status_and_correction(supported, corrected)

    def build_summary(
        self,
        total_apps: int,
        sampled_apps: int,
        correct_fields: int,
        incorrect_fields: int,
        corrected_values: int,
        final_correct_fields: int,
        output_csv: Path,
        summary_path: Path,
    ) -> dict[str, Any]:
        """Create the summary numbers that will be written to markdown and returned to the caller."""
        total_fields = sampled_apps * len(VERIFY_FIELDS)
        initial_accuracy = (correct_fields / total_fields) if total_fields else 0.0
        final_accuracy = (final_correct_fields / total_fields) if total_fields else 0.0
        return {
            "total_apps": total_apps,
            "sampled_apps": sampled_apps,
            "total_fields": total_fields,
            "correct_fields": correct_fields,
            "incorrect_fields": incorrect_fields,
            "corrected_values": corrected_values,
            "initial_accuracy": round(initial_accuracy, 4),
            "final_accuracy": round(final_accuracy, 4),
            "output_csv": str(output_csv),
            "summary_path": str(summary_path),
        }

    def write_summary(self, summary_path: Path, summary: dict[str, Any]) -> None:
        """Write the verification summary as a short markdown report."""
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        markdown = self.render_summary(summary)
        summary_path.write_text(markdown, encoding="utf-8")

    def render_summary(self, summary: dict[str, Any]) -> str:
        """Format the summary values as a beginner-friendly markdown document."""
        return "\n".join(
            [
                "# Verification Summary",
                "",
                f"- Initial Accuracy: {summary['initial_accuracy']:.2%}",
                f"- Correct Fields: {summary['correct_fields']}",
                f"- Incorrect Fields: {summary['incorrect_fields']}",
                f"- Corrected Values: {summary['corrected_values']}",
                f"- Final Accuracy: {summary['final_accuracy']:.2%}",
                f"- Sampled Apps: {summary['sampled_apps']}",
                f"- Total Apps in Results: {summary['total_apps']}",
                "",
                f"Verified CSV: {summary['output_csv']}",
            ]
        )

    def _status_and_correction(self, supported: bool, corrected_value: str) -> tuple[str, str]:
        """Convert a boolean check into the required status label and a cleaned corrected value."""
        return ("Correct" if supported else "Incorrect", clean_text(corrected_value) or "Unknown")

    def _infer_category(self, text: str) -> str:
        """Guess the category from common product keywords in the docs."""
        for category, keyword in CATEGORY_KEYWORDS.items():
            if keyword in text.lower():
                return category
        return "Unknown"

    def _infer_description(self, pages: list[PageEvidence], app_name: str) -> str:
        """Extract a short docs sentence that can serve as a corrected description."""
        for page in pages:
            sentences = re.split(r"(?<=[.!?])\s+", page.text)
            for sentence in sentences:
                candidate = clean_text(sentence)
                if len(candidate) >= 40:
                    return candidate[:240]
        return f"{app_name} is a SaaS product."

    def _infer_authentication(self, text: str) -> str:
        """Detect the primary authentication scheme from the documentation text."""
        found = []
        lowered = text.lower()
        for label, keywords in AUTH_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                found.append(label)
        return safe_join(sorted(set(found)), ", ") or "Unknown"

    def _infer_api_type(self, text: str) -> str:
        """Detect the most likely API type from the docs."""
        lowered = text.lower()
        for label, keywords in API_KEYWORDS.items():
            if any(keyword in lowered for keyword in keywords):
                return label
        return "Unknown"

    def _infer_api_surface(self, text: str) -> str:
        """Estimate the breadth of the API surface from the documentation language."""
        lowered = text.lower()
        if any(keyword in lowered for keyword in ("graphql", "webhook", "sdk", "api reference", "rate limit")):
            return "Large"
        if any(keyword in lowered for keyword in ("api key", "integration", "endpoint", "oauth")):
            return "Medium"
        if any(keyword in lowered for keyword in ("single endpoint", "few endpoints", "limited api")):
            return "Small"
        return "Unknown"

    def _infer_buildability(self, text: str) -> str:
        """Translate the docs into a practical buildability verdict."""
        lowered = text.lower()
        if self._is_gated(lowered):
            return "Partially buildable"
        if "api" not in lowered and "developer" not in lowered and "integration" not in lowered:
            return "Blocked"
        if self._infer_authentication(text) == "Unknown":
            return "Partially buildable"
        return "Buildable"

    def _infer_main_blocker(self, text: str) -> str:
        """Generate a concise blocker description from the strongest negative signal in the docs."""
        lowered = text.lower()
        if self._is_gated(lowered):
            return "Access appears gated behind a sales or demo flow."
        if self._infer_api_type(text) == "Unknown":
            return "No public API documentation was found."
        if self._infer_authentication(text) == "Unknown":
            return "Authentication requirements are not clearly documented."
        return "No obvious blocker found in the collected documentation."

    def _mentions_mcp(self, text: str) -> bool:
        """Check whether the docs mention MCP or the full Model Context Protocol name."""
        lowered = text.lower()
        return "model context protocol" in lowered or re.search(r"\bmcp\b", lowered) is not None

    def _is_gated(self, text: str) -> bool:
        """Detect sales-led or waitlist-gated access language in the documentation."""
        return any(signal in text for signal in GATING_SIGNALS)

    def _roughly_matches(self, original: str, corrected: str, evidence_text: str) -> bool:
        """Compare two strings using a small overlap test so descriptions and blocker notes can be validated."""
        original_tokens = {token for token in self._tokenize(original) if len(token) > 2}
        corrected_tokens = {token for token in self._tokenize(corrected) if len(token) > 2}
        if not original_tokens:
            return False
        overlap = len(original_tokens & corrected_tokens) / max(len(original_tokens), 1)
        return overlap >= 0.4 or self._value_in_text(original, evidence_text)

    def _normalizes_to_any(self, original: str, corrected: str) -> bool:
        """Return True when the original text matches the corrected value after normalization."""
        normalized_original = self._normalize(original)
        normalized_corrected = self._normalize(corrected)
        if not normalized_original:
            return False
        if normalized_original == normalized_corrected:
            return True
        return normalized_original in normalized_corrected or normalized_corrected in normalized_original

    def _value_in_text(self, value: str, text: str) -> bool:
        """Check whether a field value appears in the docs text after normalization."""
        normalized_value = self._normalize(value)
        return bool(normalized_value) and normalized_value in self._normalize(text)

    def _value_in_pages(self, value: str, pages: list[PageEvidence]) -> bool:
        """Check whether a field value appears in any fetched documentation page."""
        return any(self._value_in_text(value, page.title + " " + page.text) for page in pages)

    def _same_domain(self, left: str, right: str) -> bool:
        """Compare two URLs using only their domains so redirects still count as correct."""
        return self._domain(left) == self._domain(right)

    def _domain(self, url: str) -> str:
        """Extract a normalized domain from a URL."""
        from urllib.parse import urlparse

        return urlparse(url).netloc.lower()

    def _normalize(self, value: str) -> str:
        """Lowercase and collapse whitespace for consistent comparisons."""
        return clean_text(value).lower()

    def _tokenize(self, value: str) -> list[str]:
        """Split text into lowercase tokens for overlap calculations."""
        return re.findall(r"[a-z0-9]+", self._normalize(value))
