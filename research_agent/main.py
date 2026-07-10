from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

if __package__ is None or __package__ == "":
    sys.path.append(str(Path(__file__).resolve().parent.parent))
    from research_agent.agent import ResearchAgent
    from research_agent.config import ResearchConfig
    from research_agent.analytics import AnalyticsAgent
    from research_agent.verify import VerificationAgent
else:
    from .agent import ResearchAgent
    from .config import ResearchConfig
    from .analytics import AnalyticsAgent
    from .verify import VerificationAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Research SaaS apps and export the findings to CSV.")
    subparsers = parser.add_subparsers(dest="command")

    research_parser = subparsers.add_parser("research", help="Run the research pipeline.")
    research_parser.add_argument(
        "--input",
        default="data/apps.csv",
        help="Input CSV containing app_name, category, and optional homepage_url columns.",
    )
    research_parser.add_argument(
        "--output",
        default="data/results.csv",
        help="Output CSV path for the raw research results.",
    )

    verify_parser = subparsers.add_parser("verify", help="Run the verification pipeline.")
    verify_parser.add_argument(
        "--input",
        default="data/results.csv",
        help="Input CSV containing research results to verify.",
    )
    verify_parser.add_argument(
        "--output",
        default="data/verified_results.csv",
        help="Output CSV path for verified results.",
    )
    verify_parser.add_argument(
        "--summary",
        default="case_study/verification_summary.md",
        help="Markdown file path for the verification summary.",
    )
    verify_parser.add_argument(
        "--sample-size",
        type=int,
        default=20,
        help="Number of apps to randomly sample for verification.",
    )
    verify_parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed used to make the sample repeatable.",
    )

    analytics_parser = subparsers.add_parser("analytics", help="Run the analytics pipeline.")
    analytics_parser.add_argument(
        "--input",
        default="data/results.csv",
        help="Input CSV containing the research results to analyze.",
    )
    analytics_parser.add_argument(
        "--output",
        default="case_study/analytics.json",
        help="Output JSON path for the analytics payload.",
    )
    analytics_parser.add_argument(
        "--summary",
        default="case_study/analytics_summary.md",
        help="Markdown file path for the analytics summary.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, ResearchConfig().log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    command = args.command or "research"
    if command == "verify":
        verifier = VerificationAgent()
        summary = verifier.run(
            input_csv=Path(args.input),
            output_csv=Path(args.output),
            summary_path=Path(args.summary),
            sample_size=args.sample_size,
            seed=args.seed,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        logging.getLogger(__name__).info("Verification complete. Verified results written to %s", args.output)
        return 0

    if command == "analytics":
        analytics = AnalyticsAgent()
        report = analytics.run(
            input_csv=Path(args.input),
            output_json=Path(args.output),
            summary_path=Path(args.summary),
        )
        print(json.dumps(report, indent=2, ensure_ascii=False))
        logging.getLogger(__name__).info("Analytics complete. Output written to %s", args.output)
        return 0

    agent = ResearchAgent()
    results = agent.run(Path(args.input), Path(args.output))
    print(json.dumps(results, indent=2, ensure_ascii=False))
    logging.getLogger(__name__).info("Research complete. Results written to %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
