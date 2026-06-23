import json
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent


@dataclass
class ExaWebSearchTool(FunctionTool):
    plugin: Any = None
    name: str = "web_search_exa"
    description: str = "Search the web using Exa. Use for general, vertical, and concept-oriented retrieval."
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Required. Search query."},
                "max_results": {
                    "type": "number",
                    "description": (
                        "Optional. The maximum number of results to return. Default is 10."
                        " Range is 1-100."
                    ),
                },
                "search_type": {
                    "type": "string",
                    "description": (
                        "Optional. auto/keyword/neural. Default is auto. "
                        "Use auto unless the user explicitly requests keyword matching."
                    ),
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Optional. company/people/research"
                        " paper/news/personal site/financial report."
                    ),
                },
            },
            "required": ["query"],
        }
    )

    async def run(
        self,
        event: AstrMessageEvent,
        query: str,
        max_results: float = 0,
        search_type: str = "",
        category: str = "",
    ) -> str:
        from ..main import (
            PLUGIN_NAME,
            ExaAPIError,
            _normalize_count,
            _normalize_search_type,
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
                search_type=_normalize_search_type(search_type),
                category=str(category).strip(),
            )
            return json.dumps(results, ensure_ascii=False)

        except ExaAPIError as e:
            return f"Error: Exa search failed: {e}"
        except Exception as e:
            from astrbot.api import logger

            logger.error(f"[{PLUGIN_NAME}] web_search_exa exception: {e}")
            return f"Error: Exa search exception: {e}"


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
                }
            },
            "required": ["url"],
        }
    )

    async def run(
        self,
        event: AstrMessageEvent,
        url: str,
    ) -> str:
        from ..main import PLUGIN_NAME, ExaAPIError

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
            results = await plugin._exa_extract(url)
            return json.dumps(results, ensure_ascii=False)

        except ExaAPIError as e:
            return f"Error: Exa content extraction failed: {e}"
        except ValueError as e:
            return str(e)
        except Exception as e:
            from astrbot.api import logger

            logger.error(f"[{PLUGIN_NAME}] web_fetch_exa exception: {e}")
            return f"Error: Exa content extraction exception: {e}"


@dataclass
class ExaFindSimilarTool(FunctionTool):
    plugin: Any = None
    name: str = "exa_find_similar"
    description: str = (
        "Find webpages that are semantically similar to a given URL using Exa."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Required. The URL to find similar pages.",
                },
                "max_results": {
                    "type": "number",
                    "description": (
                        "Optional. The maximum number of results to return. Default is 10."
                        " Range is 1-100."
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
        max_results: float = 0,
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

        if not max_results:
            max_results = plugin.config.get("max_results", 10)

        try:
            num = _normalize_count(max_results, default=10, minimum=1, maximum=100)
            results = await plugin._exa_find_similar(url, num_results=num)
            return json.dumps(results, ensure_ascii=False)

        except ExaAPIError as e:
            return f"Error: Exa find similar failed: {e}"
        except Exception as e:
            from astrbot.api import logger

            logger.error(f"[{PLUGIN_NAME}] exa_find_similar exception: {e}")
            return f"Error: Exa find similar exception: {e}"
