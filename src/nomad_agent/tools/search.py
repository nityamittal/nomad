"""Web search via DuckDuckGo's free HTML endpoint — no API key.

Results are external, untrusted content; the agent loop wraps them in a
marker so the model treats them as data, not instructions (Phase 3).
"""

from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request

from .base import Tool, ToolResult

_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r".*?"
    r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(fragment: str) -> str:
    return html.unescape(_TAG_RE.sub("", fragment)).strip()


def _real_url(href: str) -> str:
    """DDG wraps result links in a redirect (//duckduckgo.com/l/?uddg=<url>)."""
    parsed = urllib.parse.urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        query = urllib.parse.parse_qs(parsed.query)
        if "uddg" in query:
            return query["uddg"][0]
    return href


def parse_ddg_html(page: str, max_results: int = 5) -> list[dict]:
    results = []
    for match in _RESULT_RE.finditer(page):
        results.append(
            {
                "title": _clean(match.group("title")),
                "url": _real_url(match.group("url")),
                "snippet": _clean(match.group("snippet")),
            }
        )
        if len(results) >= max_results:
            break
    return results


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web (DuckDuckGo). Returns titles, URLs and snippets."
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "description": "default 5"},
        },
        "required": ["query"],
    }

    endpoint = "https://html.duckduckgo.com/html/"

    def _fetch(self, query: str) -> str:
        request = urllib.request.Request(
            f"{self.endpoint}?{urllib.parse.urlencode({'q': query})}",
            headers={"User-Agent": "Mozilla/5.0 (nomad-agent)"},
        )
        with urllib.request.urlopen(request, timeout=30) as resp:
            return resp.read().decode("utf-8", errors="replace")

    def execute(self, args: dict) -> ToolResult:
        try:
            page = self._fetch(args["query"])
        except OSError as exc:
            return ToolResult(f"Web search failed: {exc}", error=True)
        results = parse_ddg_html(page, int(args.get("max_results", 5)))
        if not results:
            return ToolResult("No results found.")
        lines = [
            f"{i + 1}. {r['title']}\n   {r['url']}\n   {r['snippet']}"
            for i, r in enumerate(results)
        ]
        return ToolResult("\n".join(lines))
