from __future__ import annotations

RESULT_SCHEMA = {
    "app_name": "string",
    "category": "string",
    "one_line_description": "string",
    "authentication_method": "string",
    "self_serve_or_gated": "Self Serve | Gated | Unknown",
    "api_type": "string",
    "api_surface": "Small | Medium | Large | Unknown",
    "existing_mcp": "Yes | No | Unknown",
    "buildability_verdict": "Buildable | Partially buildable | Blocked | Unknown",
    "main_blocker": "string",
    "evidence_url": "string",
}

SYSTEM_PROMPT = """You are a careful SaaS research analyst.
Use only the evidence provided to classify the product.
Return concise, factual answers.
If evidence is missing, use Unknown instead of guessing.
"""

USER_PROMPT_TEMPLATE = """Research the following SaaS product and return a JSON object with these fields:

- app_name
- category
- one_line_description
- authentication_method
- self_serve_or_gated
- api_type
- api_surface
- existing_mcp
- buildability_verdict
- main_blocker
- evidence_url

App name: {app_name}
Category from input: {category}
Official URL: {official_url}

Evidence:
{evidence}

Rules:
- Keep the description to one sentence.
- Use the official docs or product pages as evidence.
- Set existing_mcp to Yes only if the official site explicitly mentions MCP or Model Context Protocol.
- Set buildability_verdict to Blocked if there is no usable public API or access is clearly gated.
- Set main_blocker to the most important reason a builder could not proceed.
- Return JSON only.
"""
