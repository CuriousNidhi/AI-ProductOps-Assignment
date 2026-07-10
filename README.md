# AI Product Ops Assignment

This repository is the Phase 1 scaffold for an AI research agent that can research SaaS products, classify their API and authentication surface, verify a random sample against official documentation, and export the findings to CSV.

# AI Product Ops Assignment

## Live Demo
https://ai-product-ops-assignment-te5y.vercel.app/

## GitHub Repository
https://github.com/CuriousNidhi/AI-ProductOps-Assignment

## Overview

This repository contains an AI-powered research agent that analyzes 100 SaaS applications, verifies the collected data, generates analytics, and presents the findings in an interactive HTML dashboard.

## What this project does

The first version is designed to:

1. Read a list of SaaS apps from `data/apps.csv`.
2. Visit official sources such as the homepage, docs, and API pages.
3. Extract evidence from those pages.
4. Classify the app into the requested research columns.
5. Print the full batch as JSON to stdout.
6. Write the output to `data/results.csv` and later to `data/verified_results.csv`.
7. Sample 20 apps from `data/results.csv`, verify them against official docs, and write a verification summary.

## Folder structure

- `research_agent/` contains the Python code for the research pipeline.
- `data/` stores the input list and the generated CSV outputs.
- `case_study/` will hold the final HTML case study in later phases.
- `screenshots/` will hold visual evidence captured during verification.

## File purpose

### `research_agent/main.py`
Command-line entry point. This is the file you run to start either the research workflow or the verification workflow.

### `research_agent/agent.py`
Orchestrates the research workflow. It loads apps, gathers evidence, classifies the results, and writes CSV output.

### `research_agent/search.py`
Handles web discovery and page fetching. It is responsible for finding official URLs and collecting evidence from the web.

### `research_agent/prompts.py`
Stores the prompt templates used when the OpenAI API is available.

### `research_agent/utils.py`
Shared helper functions for file handling, CSV operations, text cleanup, and general utilities.

### `research_agent/verify.py`
Verification pipeline. It samples result rows, fetches the official documentation, checks each field, writes `verified_results.csv`, and produces a summary report.

### `research_agent/analytics.py`
Analytics pipeline. It reads `results.csv`, computes dashboard statistics, and exports a JSON payload for the HTML case study.

### `research_agent/config.py`
Central configuration for paths, environment variables, and research settings.

### `data/apps.csv`
Input list of SaaS apps to research. Start by adding one app per row.

### `data/results.csv`
Raw research output from the agent.

### `data/verified_results.csv`
Cleaned and manually verified output for the final analysis phase.

## Setup

1. Install Python 3.12.
2. Create and activate a virtual environment.
3. Install dependencies with `pip install -r requirements.txt`.
4. Add your OpenAI key to a `.env` file if you want LLM-assisted classification.
5. Add your app list to `data/apps.csv`.
6. Run the agent from the repository root.

## Analytics mode

Run analytics after you have data in `data/results.csv`:

```bash
python research_agent/main.py analytics
```

This will:

1. Calculate authentication distribution.
2. Compare Self Serve vs Gated.
3. Split API types into REST, GraphQL, and Others.
4. Measure MCP availability.
5. Show category distribution.
6. Rank the most common blockers.
7. Estimate buildability percentage.
8. Identify easy-win and difficult categories.
9. Write `case_study/analytics.json` and `case_study/analytics_summary.md`.

## Environment variables

- `OPENAI_API_KEY`: enables OpenAI-assisted classification.
- `OPENAI_MODEL`: optional model name, defaults to a compact GPT-5 class model if not set.
- `USER_AGENT`: optional custom user agent for web requests.
- `REQUEST_TIMEOUT_SECONDS`: optional timeout for HTTP requests.

## Expected CSV columns

The generated research output uses these columns:

- `App Name`
- `Category`
- `One-line description`
- `Authentication method`
- `Self Serve or Gated`
- `API Type`
- `API Surface`
- `Existing MCP`
- `Buildability Verdict`
- `Main Blocker`
- `Evidence URL`

## Next phase

Later we will add verification, analysis, and the HTML case study with Tailwind CSS and Chart.js.

## Verification mode

Run verification after you have some rows in `data/results.csv`:

```bash
python research_agent/main.py verify
```

This will:

1. Randomly sample up to 20 apps.
2. Open each app's official documentation URL.
3. Compare each extracted field against the docs.
4. Mark each field as Correct or Incorrect.
5. Write `data/verified_results.csv`.
6. Write `case_study/verification_summary.md`.

## What each insight means

- Authentication distribution: which login or token pattern appears most often.
- Self Serve vs Gated: how often an app can be used directly versus requiring sales contact or a demo.
- REST vs GraphQL vs Others: which API style is most common in the dataset.
- MCP availability: whether the docs mention MCP or Model Context Protocol.
- Category distribution: which SaaS categories show up most often.
- Most common blockers: the most frequent reasons an app is hard to integrate.
- Buildability percentage: the share of apps that look buildable or partially buildable from the public evidence.
- Easy-win categories: categories with the highest buildable rate.
- Difficult integration categories: categories with the lowest buildable rate.

## Run the Project

Install dependencies:

```bash
pip install -r requirements.txt
```

Run research:

```bash
python research_agent/main.py research
```

Run verification:

```bash
python research_agent/main.py verify
```

Run analytics:

```bash
python research_agent/main.py analytics
```

View the HTML dashboard:

```bash
python -m http.server 8000
```

Open:

```
http://localhost:8000/case_study/index.html
```
