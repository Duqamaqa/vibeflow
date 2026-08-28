"""Cited live-web research through OpenRouter models exposed by FCC."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from .fcc_client import FCCClient


_MARKDOWN_URL = re.compile(r"\[[^\]]+\]\((https?://[^\s)]+)\)")
_PLAIN_URL = re.compile(r"https?://[^\s<>\])}]+")


@dataclass(frozen=True, slots=True)
class ResearchSource:
    title: str
    url: str

    def to_dict(self) -> dict[str, str]:
        return {"title": self.title, "url": self.url}


@dataclass(frozen=True, slots=True)
class ResearchResult:
    report: str
    sources: tuple[ResearchSource, ...]
    model: str
    usage: Mapping[str, Any]
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "report": self.report,
            "sources": [source.to_dict() for source in self.sources],
            "model": self.model,
            "usage": dict(self.usage),
            "request_id": self.request_id,
        }


class ResearchError(RuntimeError):
    """Live research did not return safely attributable evidence."""


class OpenRouterResearcher:
    """Run one bounded OpenRouter web-search request through FCC."""

    def __init__(self, client: FCCClient, *, max_results: int = 8) -> None:
        if not 1 <= max_results <= 10:
            raise ValueError("max_results must be between 1 and 10")
        self.client = client
        self.max_results = max_results

    def research(self, goal: str, model: str) -> ResearchResult:
        base_model = model.removesuffix(":online")
        if "open_router/" not in base_model.lower():
            raise ResearchError("Research model must use OpenRouter through FCC")
        online_model = f"{base_model}:online"
        prompt = (
            "Conduct live web research for the request below. Use current public sources. "
            "Do not invent businesses, website status, demand, contact details, or quotations. "
            "For every material factual claim, include a direct Markdown source link. "
            "Clearly label inference separately from sourced fact. Prefer official business pages, "
            "maps/listing pages, and primary sources. Return a concise actionable report.\n\n"
            f"REQUEST:\n{goal}"
        )
        response = self.client.create_response(
            model=online_model,
            input=prompt,
            stream=True,
            plugins=[{"id": "web", "max_results": self.max_results}],
        )
        report = response.text.strip()
        if not report:
            raise ResearchError("Web research returned no report")
        sources = _extract_sources(report, response.raw)
        if not sources:
            raise ResearchError("Web research returned no safe source URLs")
        return ResearchResult(
            report=report,
            sources=sources,
            model=online_model,
            usage=dict(response.usage or {}),
            request_id=response.request_id,
        )


def _extract_sources(report: str, raw: Any) -> tuple[ResearchSource, ...]:
    candidates: list[tuple[str, str]] = []
    _collect_annotations(raw, candidates)
    for url in _MARKDOWN_URL.findall(report):
        candidates.append((_title_for_url(url), url))
    for url in _PLAIN_URL.findall(report):
        candidates.append((_title_for_url(url), url))

    sources: list[ResearchSource] = []
    seen: set[str] = set()
    for title, candidate in candidates:
        url = _safe_public_url(candidate)
        if url is None or url in seen:
            continue
        seen.add(url)
        sources.append(ResearchSource(title.strip() or _title_for_url(url), url))
    return tuple(sources[:10])


def _collect_annotations(value: Any, candidates: list[tuple[str, str]]) -> None:
    if isinstance(value, Mapping):
        citation = value.get("url_citation")
        if isinstance(citation, Mapping) and isinstance(citation.get("url"), str):
            candidates.append((str(citation.get("title", "Source")), citation["url"]))
        if value.get("type") == "url_citation" and isinstance(value.get("url"), str):
            candidates.append((str(value.get("title", "Source")), value["url"]))
        for item in value.values():
            _collect_annotations(item, candidates)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_annotations(item, candidates)


def _safe_public_url(candidate: str) -> str | None:
    url = candidate.strip().rstrip(".,;:")
    if len(url) > 2048:
        return None
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return url


def _title_for_url(url: str) -> str:
    return urlsplit(url).hostname or "Source"
