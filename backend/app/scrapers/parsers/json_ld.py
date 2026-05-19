import json
from html.parser import HTMLParser
from typing import Any


class JsonLdExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_json_ld = False
        self._chunks: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {name.lower(): value for name, value in attrs}
        if tag == "script" and attr_map.get("type") == "application/ld+json":
            self._in_json_ld = True
            self._chunks = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_json_ld:
            self.blocks.append("".join(self._chunks))
            self._in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_json_ld:
            self._chunks.append(data)


def extract_job_postings(html: str) -> list[dict[str, Any]]:
    parser = JsonLdExtractor()
    parser.feed(html)
    postings: list[dict[str, Any]] = []
    for block in parser.blocks:
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        for item in _flatten_json_ld(payload):
            if item.get("@type") == "JobPosting":
                postings.append(item)
    return postings


def _flatten_json_ld(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        items: list[dict[str, Any]] = []
        for entry in payload:
            items.extend(_flatten_json_ld(entry))
        return items
    if not isinstance(payload, dict):
        return []
    graph = payload.get("@graph")
    if isinstance(graph, list):
        return [entry for entry in graph if isinstance(entry, dict)]
    return [payload]
