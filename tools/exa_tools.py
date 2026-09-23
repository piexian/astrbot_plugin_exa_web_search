import json
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent

from .exa_context import format_context_result
from .exa_search import SEARCH_CATEGORIES, SEARCH_TYPES, normalize_search_type


@dataclass
class ExaSearchTool(FunctionTool):
    plugin: Any = None
    name: str = "exa-search"
    description: str = "Search the web using Exa. Use for general, vertical, and concept-oriented retrieval."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Required. Search query."},
                "max_results": {
                    "type": "integer",
                    "description": (
                        "Optional. The maximum number of results to return. Default is 10."
                        " Range is 1-100."
                    ),
                },
                "search_type": {
                    "type": "string",
                    "description": (
                        "Optional. instant/fast/auto/deep-lite/deep/deep-reasoning. "
                        "Default is auto."
                    ),
                    "enum": sorted(SEARCH_TYPES),
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Optional. company/publication/news/personal site/"
                        "financial report/people."
                    ),
                    "enum": sorted(SEARCH_CATEGORIES),
                },
                "include_domains": {
                    "type": "string",
                    "description": (
                        "Optional. Comma-separated domains to restrict results to."
                    ),
                },
                "exclude_domains": {
                    "type": "string",
                    "description": (
                        "Optional. Comma-separated domains to exclude from results."
                    ),
                },
                "start_published_date": {
                    "type": "string",
                    "description": (
                        "Optional. Start date filter in ISO 8601 format "
                        "(e.g. 2024-01-01T00:00:00.000Z)."
                    ),
                },
                "end_published_date": {
                    "type": "string",
                    "description": "Optional. End date filter in ISO 8601 format.",
                },
            },
            "required": ["query"],
        }
    )

    async def run(
        self,
        event: AstrMessageEvent,
        query: str,
        max_results: int = 0,
        search_type: str = "",
        category: str = "",
        include_domains: str = "",
        exclude_domains: str = "",
        start_published_date: str = "",
        end_published_date: str = "",
    ) -> str:
        from ..main import (
            PLUGIN_NAME,
            ExaAPIError,
            _normalize_count,
        )

        plugin = self.plugin
        if plugin is None:
            return "Error: Plugin instance not initialized in tool."

        keys = plugin.config.get("exa_api_keys", [])
        if not keys:
            return "Error: Exa API key is not configured."

        if not max_results:
            max_results = plugin.config.get("max_results", 10)
        if not search_type:
            search_type = plugin.config.get("default_search_type", "auto")

        try:
            num = _normalize_count(max_results, default=10, minimum=1, maximum=100)
            results = await plugin._exa_search(
                query,
                num_results=num,
                search_type=normalize_search_type(search_type),
                category=str(category).strip(),
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                start_published_date=str(start_published_date).strip(),
                end_published_date=str(end_published_date).strip(),
            )
            return json.dumps(results, ensure_ascii=False)

        except ExaAPIError as e:
            return f"Error: Exa search failed: {e}"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            from astrbot.api import logger

            logger.error(f"[{PLUGIN_NAME}] exa-search exception: {e}")
            return f"Error: Exa search exception: {e}"


@dataclass
class ExaCodeContextTool(FunctionTool):
    plugin: Any = None
    name: str = "exa-code-context"
    description: str = (
        "Find token-efficient code examples and implementation context using Exa Code."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Required. Code implementation or technical documentation query.",
                },
                "tokens_num": {
                    "description": (
                        "Optional. Use dynamic or an integer from 50 to 100000."
                    ),
                    "oneOf": [
                        {"type": "string", "enum": ["dynamic"]},
                        {"type": "integer", "minimum": 50, "maximum": 100000},
                    ],
                },
            },
            "required": ["query"],
        }
    )

    async def run(
        self,
        event: AstrMessageEvent,
        query: str,
        tokens_num: str | int = "dynamic",
    ) -> str:
        from ..main import PLUGIN_NAME, ExaAPIError

        plugin = self.plugin
        if plugin is None:
            return "Error: Plugin instance not initialized in tool."
        if not plugin.config.get("exa_api_keys", []):
            return "Error: Exa API key is not configured."

        try:
            data = await plugin._exa_code_context(query, tokens_num=tokens_num)
            return format_context_result(data, query)
        except ExaAPIError as e:
            return f"Error: Exa Code Context failed: {e}"
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            from astrbot.api import logger

            logger.error(f"[{PLUGIN_NAME}] exa-code-context exception: {e}")
            return f"Error: Exa Code Context exception: {e}"


@dataclass
class ExaWebFetchTool(FunctionTool):
    plugin: Any = None
    name: str = "web_fetch_exa"
    description: str = "Fetch the full text content of a web page using Exa."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Required. Full HTTP/HTTPS URL.",
                },
                "max_characters": {
                    "type": "integer",
                    "description": (
                        "Optional. Maximum number of characters to return."
                        " Default is 3000."
                    ),
                },
            },
            "required": ["url"],
        }
    )

    async def run(
        self,
        event: AstrMessageEvent,
        url: str,
        max_characters: int = 0,
    ) -> str:
        from ..main import PLUGIN_NAME, ExaAPIError, _normalize_count

        plugin = self.plugin
        if plugin is None:
            return "Error: Plugin instance not initialized in tool."

        keys = plugin.config.get("exa_api_keys", [])
        if not keys:
            return "Error: Exa API key is not configured."

        url = str(url).strip()
        if not url:
            return "Error: URL is required."

        try:
            max_chars = _normalize_count(
                max_characters or 3000,
                default=3000,
                minimum=1,
                maximum=100000,
            )
            results = await plugin._exa_extract(url, max_characters=max_chars)
            return json.dumps(results, ensure_ascii=False)

        except ExaAPIError as e:
            return f"Error: Exa content extraction failed: {e}"
        except ValueError as e:
            return str(e)
        except Exception as e:
            from astrbot.api import logger

            logger.error(f"[{PLUGIN_NAME}] web_fetch_exa exception: {e}")
            return f"Error: Exa content extraction exception: {e}"
