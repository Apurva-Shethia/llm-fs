"""
Web Search MCP Server (bonus) — mock external knowledge source for multi-MCP demos.

Provides simple web-style search results for skill/market context that the
matching agent can combine with filesystem MCP resources.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(message)s")

mcp = FastMCP(
    "web-search-mcp-server",
    instructions="Mock web search MCP server for skill and role market context.",
)

# Curated knowledge base for demo searches
KNOWLEDGE_BASE: Dict[str, List[Dict[str, str]]] = {
    "tensorflow": [
        {
            "title": "TensorFlow in Production ML Pipelines",
            "snippet": "TensorFlow remains a top framework for deep learning in computer vision and NLP roles.",
            "url": "https://example.com/tensorflow-production",
        },
        {
            "title": "TensorFlow vs PyTorch Hiring Trends 2025",
            "snippet": "Both frameworks are in demand; TensorFlow leads in enterprise ML infrastructure teams.",
            "url": "https://example.com/tf-pytorch-trends",
        },
    ],
    "react": [
        {
            "title": "React Developer Market Demand",
            "snippet": "React skills are among the most requested for frontend and full-stack engineering roles.",
            "url": "https://example.com/react-demand",
        },
    ],
    "machine learning": [
        {
            "title": "ML Engineer Role Requirements",
            "snippet": "Typical ML roles require Python, model deployment, and experience with TensorFlow or PyTorch.",
            "url": "https://example.com/ml-requirements",
        },
    ],
    "kubernetes": [
        {
            "title": "Kubernetes for Platform Engineering",
            "snippet": "Kubernetes experience is a common must-have for senior backend and DevOps positions.",
            "url": "https://example.com/k8s-platform",
        },
    ],
}


@mcp.resource("websearch://index")
def search_index() -> str:
    """List available search topics."""
    return json.dumps({"topics": sorted(KNOWLEDGE_BASE.keys())}, indent=2)


@mcp.tool()
def web_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Search the mock knowledge base for skill and role context."""
    lowered = query.lower()
    hits: List[Dict[str, str]] = []

    for topic, entries in KNOWLEDGE_BASE.items():
        if topic in lowered:
            hits.extend(entries)

    if not hits:
        hits = [
            {
                "title": f"No exact matches for '{query}'",
                "snippet": "Try searching for TensorFlow, React, machine learning, or Kubernetes.",
                "url": "https://example.com/no-results",
            }
        ]

    return {
        "success": True,
        "query": query,
        "results": hits[:max_results],
        "result_count": min(len(hits), max_results),
    }


@mcp.tool()
def enrich_skill_context(skills: List[str]) -> Dict[str, Any]:
    """Return market context for a list of skills (used by the matching agent)."""
    enriched: Dict[str, List[Dict[str, str]]] = {}
    for skill in skills:
        key = skill.lower()
        matched = None
        for topic, entries in KNOWLEDGE_BASE.items():
            if topic in key or key in topic:
                matched = entries
                break
        enriched[skill] = matched or [
            {
                "title": f"{skill} — general context",
                "snippet": f"{skill} is a relevant technical skill for engineering roles.",
                "url": f"https://example.com/skills/{key.replace(' ', '-')}",
            }
        ]

    return {"success": True, "skills": skills, "context": enriched}


if __name__ == "__main__":
    mcp.run(transport="stdio")
