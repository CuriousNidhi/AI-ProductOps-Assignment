from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ANALYTICS_JSON, ANALYTICS_SUMMARY_MD, RESULTS_CSV, ResearchConfig
from .utils import clean_text, read_csv_records


INSIGHT_EXPLANATIONS = {
    "authentication_distribution": "Shows which authentication patterns appear most often across the researched apps. This helps identify the dominant integration style.",
    "self_serve_vs_gated": "Shows how many products are immediately usable versus sales-led or access-restricted. This is a quick signal for integration friction.",
    "api_type_distribution": "Compares REST, GraphQL, and other API styles. This helps the dashboard show which API styles are most common.",
    "mcp_availability": "Shows whether the source material explicitly mentions MCP or Model Context Protocol. This indicates whether a native MCP integration already exists.",
    "category_distribution": "Shows which SaaS categories appear most frequently in the dataset.",
    "most_common_blockers": "Lists the top reasons apps are hard to integrate. This helps prioritize product and tooling work.",
    "buildability_percentage": "Shows the share of apps that are realistically buildable or partially buildable from the available public evidence.",
    "easy_win_categories": "Highlights categories with the highest buildability rate. These are the best candidates for quick wins.",
    "difficult_integration_categories": "Highlights categories with the lowest buildability rate. These are the hardest areas to automate or integrate.",
}


class AnalyticsAgent:
    def __init__(self, config: ResearchConfig | None = None) -> None:
        self.config = config or ResearchConfig()

    def run(
        self,
        input_csv: Path = RESULTS_CSV,
        output_json: Path = ANALYTICS_JSON,
        summary_path: Path = ANALYTICS_SUMMARY_MD,
    ) -> dict[str, Any]:
        """Load result rows, compute dashboard metrics, and write the JSON plus summary files."""
        rows = self.load_results(input_csv)
        analytics = self.compute_analytics(rows, input_csv)
        self.write_json(output_json, analytics)
        self.write_summary(summary_path, analytics)
        return analytics

    def load_results(self, path: Path) -> list[dict[str, str]]:
        """Read the research output CSV into a normalized list of dictionaries."""
        return read_csv_records(path)

    def compute_analytics(self, rows: list[dict[str, str]], input_csv: Path) -> dict[str, Any]:
        """Compute the grouped counts, percentages, and ranked categories used by the dashboard."""
        total_apps = len(rows)
        auth_counts = self.count_values(rows, "Authentication method")
        serve_counts = self.count_values(rows, "Self Serve or Gated")
        api_counts = self.count_api_types(rows)
        mcp_counts = self.count_values(rows, "Existing MCP")
        category_counts = self.count_values(rows, "Category")
        blocker_counts = self.count_values(rows, "Main Blocker")
        buildability_counts = self.count_values(rows, "Buildability Verdict")

        buildable_rate = self.calculate_rate(buildability_counts.get("Buildable", 0), total_apps)
        partial_rate = self.calculate_rate(buildability_counts.get("Partially buildable", 0), total_apps)
        buildability_percentage = round(buildable_rate + partial_rate, 2)

        easy_win_categories = self.rank_categories_by_buildability(rows, descending=True)
        difficult_categories = self.rank_categories_by_buildability(rows, descending=False)

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_csv": str(input_csv),
            "total_apps": total_apps,
            "insight_explanations": INSIGHT_EXPLANATIONS,
            "insights": {
                "authentication_distribution": self.build_distribution_payload(auth_counts, total_apps),
                "self_serve_vs_gated": self.build_distribution_payload(serve_counts, total_apps),
                "api_type_distribution": self.build_distribution_payload(api_counts, total_apps),
                "mcp_availability": self.build_distribution_payload(mcp_counts, total_apps),
                "category_distribution": self.build_distribution_payload(category_counts, total_apps),
                "most_common_blockers": self.build_ranked_payload(blocker_counts, total_apps, top_n=10),
                "buildability_percentage": {
                    "buildable": buildability_counts.get("Buildable", 0),
                    "partially_buildable": buildability_counts.get("Partially buildable", 0),
                    "blocked": buildability_counts.get("Blocked", 0),
                    "buildable_percentage": round(buildable_rate, 2),
                    "partially_buildable_percentage": round(partial_rate, 2),
                    "buildability_percentage": buildability_percentage,
                },
                "easy_win_categories": easy_win_categories,
                "difficult_integration_categories": difficult_categories,
            },
        }

    def count_values(self, rows: list[dict[str, str]], column: str) -> Counter[str]:
        """Count non-empty values for a specific CSV column."""
        values = [self.normalize_label(row.get(column)) for row in rows if self.normalize_label(row.get(column))]
        return Counter(values)

    def count_api_types(self, rows: list[dict[str, str]]) -> Counter[str]:
        """Normalize API type values so REST, GraphQL, and everything else can be compared consistently."""
        values: list[str] = []
        for row in rows:
            value = self.normalize_label(row.get("API Type")).lower()
            if not value:
                continue
            if value == "graphql":
                values.append("GraphQL")
            elif value == "rest":
                values.append("REST")
            else:
                values.append("Others")
        return Counter(values)

    def build_distribution_payload(self, counts: Counter[str], total_apps: int) -> list[dict[str, Any]]:
        """Convert a Counter into a sorted list of label/count/percentage records."""
        if not counts:
            return []
        return [
            {
                "label": label,
                "count": count,
                "percentage": round(self.calculate_rate(count, total_apps), 2),
            }
            for label, count in counts.most_common()
        ]

    def build_ranked_payload(self, counts: Counter[str], total_apps: int, top_n: int = 5) -> list[dict[str, Any]]:
        """Convert a Counter into the top-ranked items for dashboard display."""
        if not counts:
            return []
        return [
            {
                "label": label,
                "count": count,
                "percentage": round(self.calculate_rate(count, total_apps), 2),
            }
            for label, count in counts.most_common(top_n)
        ]

    def rank_categories_by_buildability(self, rows: list[dict[str, str]], descending: bool) -> list[dict[str, Any]]:
        """Rank categories by their buildable share so the dashboard can show easy wins and hard cases."""
        category_totals: dict[str, int] = defaultdict(int)
        category_buildable: dict[str, int] = defaultdict(int)

        for row in rows:
            category = self.normalize_label(row.get("Category")) or "Unknown"
            verdict = self.normalize_label(row.get("Buildability Verdict")).lower()
            category_totals[category] += 1
            if verdict == "buildable":
                category_buildable[category] += 1

        ranked: list[dict[str, Any]] = []
        for category, total in category_totals.items():
            buildable = category_buildable.get(category, 0)
            buildable_rate = self.calculate_rate(buildable, total)
            ranked.append(
                {
                    "category": category,
                    "total_apps": total,
                    "buildable_apps": buildable,
                    "buildable_rate": round(buildable_rate, 2),
                }
            )

        ranked.sort(key=lambda item: (item["buildable_rate"], item["total_apps"], item["category"]), reverse=descending)
        return ranked[:5]

    def calculate_rate(self, count: int, total: int) -> float:
        """Convert a count into a percentage of the total, safely handling empty inputs."""
        if total <= 0:
            return 0.0
        return (count / total) * 100

    def write_json(self, path: Path, payload: dict[str, Any]) -> None:
        """Persist the analytics JSON payload for the HTML dashboard."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_pretty_json(payload), encoding="utf-8")

    def write_summary(self, path: Path, payload: dict[str, Any]) -> None:
        """Write a short markdown summary that explains each insight in plain English."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.render_summary(payload), encoding="utf-8")

    def render_summary(self, payload: dict[str, Any]) -> str:
        """Generate a beginner-friendly explanation of each analytical insight."""
        lines = ["# Analytics Summary", ""]
        lines.append(f"- Total apps analyzed: {payload['total_apps']}")
        lines.append("")
        lines.append("## Insight Meanings")
        lines.append("")
        for key, explanation in INSIGHT_EXPLANATIONS.items():
            lines.append(f"- {self.humanize_key(key)}: {explanation}")
        lines.append("")
        lines.append(f"Source CSV: {payload['source_csv']}")
        return "\n".join(lines)

    def to_pretty_json(self, payload: dict[str, Any]) -> str:
        """Serialize the analytics payload in a stable, dashboard-friendly JSON format."""
        import json

        return json.dumps(payload, indent=2, ensure_ascii=False)

    def normalize_label(self, value: Any) -> str:
        """Normalize labels so grouping is case-insensitive and whitespace-safe."""
        return clean_text(str(value or "")).strip()

    def humanize_key(self, key: str) -> str:
        """Turn a snake_case insight key into a human-readable label."""
        return key.replace("_", " ").title()
