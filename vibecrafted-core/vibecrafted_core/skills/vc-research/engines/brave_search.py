#!/usr/bin/env python3
"""Brave Search API client for Claude Code skill.

Usage:
    python3 brave_search.py "search query" [--count N] [--lang LANG]

Created by Vetcoders (c)2024-2026 Vetcoders
"""

import http.client
import json
import os
import ssl
import urllib.parse

API_HOST = "api.search.brave.com"
API_PATH = "/res/v1/web/search"


def build_tls_context() -> ssl.SSLContext:
    """Return a default (verifying) SSL context for the Brave Search HTTPS connection."""
    return ssl.create_default_context()


def search(query: str, count: int = 8, lang: str | None = None) -> dict:
    """Call the Brave Search web API and return the decoded JSON response.

    Returns a dict with an "error" key (never raises) when the API key is
    missing, the HTTP call fails, or the response is not valid JSON.
    """
    api_key = os.getenv("BRAVE_SEARCH_API_KEY") or os.getenv("BRAVE_API_KEY")
    if not api_key:
        return {
            "error": (
                "Missing Brave Search API key. Set BRAVE_SEARCH_API_KEY "
                "(or BRAVE_API_KEY) in the environment."
            )
        }

    params = {"q": query, "count": str(count)}
    if lang:
        params["search_lang"] = lang

    path = f"{API_PATH}?{urllib.parse.urlencode(params)}"
    # TLS verification is enforced via the explicit default SSL context below.
    # nosemgrep: python.lang.security.audit.httpsconnection-detected.httpsconnection-detected
    conn = http.client.HTTPSConnection(
        API_HOST, timeout=15, context=build_tls_context()
    )
    headers = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "X-Subscription-Token": api_key,
    }

    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode()
        if resp.status >= 400:
            return {"error": f"HTTP {resp.status}: {resp.reason}"}
        return json.loads(body)
    except Exception as e:  # noqa: BLE001 - network or JSON failure is returned to the research lane as data
        return {"error": str(e)}
    finally:
        conn.close()


def format_results(data: dict) -> str:
    """Render a Brave Search response dict as a human-readable text listing."""
    if "error" in data:
        return f"Error: {data['error']}"

    lines = []
    web = data.get("web", {}).get("results", [])
    if not web:
        return "No results found."

    for i, r in enumerate(web, 1):
        title = r.get("title", "No title")
        url = r.get("url", "")
        desc = r.get("description", "No description")
        lines.append(f"[{i}] {title}")
        lines.append(f"    {url}")
        lines.append(f"    {desc}")
        lines.append("")

    # Append news if present
    news = data.get("news", {}).get("results", [])
    if news:
        lines.append("--- News ---")
        for r in news[:3]:
            lines.append(f"  - {r.get('title', '')} ({r.get('url', '')})")
        lines.append("")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Brave Search")
    parser.add_argument("query", nargs="+", help="Search query")
    parser.add_argument("--count", "-c", type=int, default=8)
    parser.add_argument("--lang", "-l", default=None)
    args = parser.parse_args()

    query = " ".join(args.query)
    data = search(query, count=args.count, lang=args.lang)
    print(format_results(data))
