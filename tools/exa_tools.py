import json
from dataclasses import dataclass, field
from typing import Any

from astrbot.api import FunctionTool
from astrbot.api.event import AstrMessageEvent

from .exa_agent import AGENT_EFFORTS
from .exa_context import format_context_result
from .exa_search import SEARCH_CATEGORIES, SEARCH_TYPES, normalize_search_type


def build_search_tool_payload(data: dict) -> dict:
    """Assemble the LLM tool JSON from a search response."""
    payload: dict = {"results": data.get("results", [])}
    notices = data.get("notices")
    if isinstance(notices, list) and notices:
        payload["notices"] = notices
    output = data.get("output")
    if isinstance(output, dict) and output:
        payload["output"] = output
    return payload


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
                "user_location": {
                    "type": "string",
                    "description": "Optional. Two-letter ISO user location, such as US.",
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
                "objective": {
                    "type": "string",
                    "description": (
                        "Optional. Goal for this search turn: which documents "
                        "should rank first, which to exclude, what facts to pull."
                    ),
                },
                "system_prompt": {
                    "type": "string",
                    "description": (
                        "Optional. Extra instructions guiding source "
                        "preferences or output behavior."
                    ),
                },
                "output_schema": {
                    "type": "object",
                    "description": (
                        'Optional. JSON Schema with root type "text" or '
                        '"object"; the response then includes synthesized '
                        "output."
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
        max_results: int = 0,
        search_type: str = "",
        category: str = "",
        user_location: str = "",
        include_domains: str = "",
        exclude_domains: str = "",
        start_published_date: str = "",
        end_published_date: str = "",
        objective: str = "",
        system_prompt: str = "",
        output_schema: object = None,
    ) -> str:
        from ..main import (
            PLUGIN_NAME,
            ExaAPIError,
            _derive_user_identifier,
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

        user_identifier = ""
        if plugin.config.get("send_user_identifier", False):
            user_identifier = _derive_user_identifier(event)

        try:
            num = _normalize_count(max_results, default=10, minimum=1, maximum=100)
            data = await plugin._exa_search_response(
                query,
                num_results=num,
                search_type=normalize_search_type(search_type),
                category=str(category).strip(),
                user_location=str(user_location).strip(),
                include_domains=include_domains,
                exclude_domains=exclude_domains,
                start_published_date=str(start_published_date).strip(),
                end_published_date=str(end_published_date).strip(),
                objective=str(objective or "").strip(),
                system_prompt=str(system_prompt or "").strip(),
                output_schema=output_schema,
                user_identifier=user_identifier,
            )
            return json.dumps(build_search_tool_payload(data), ensure_ascii=False)
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
                "max_age_hours": {
                    "type": "integer",
                    "minimum": -1,
                    "maximum": 720,
                    "description": "Optional. Content freshness in hours; 0 always fetches fresh content.",
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
        max_age_hours: int | None = None,
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
            results = await plugin._exa_extract(
                url,
                max_characters=max_chars,
                max_age_hours=max_age_hours,
            )
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
class ExaAgentSearchTool(FunctionTool):
    plugin: Any = None
    name: str = "exa-agent-search"
    description: str = (
        "Launch a background Exa Agent deep-research task. Returns the task id "
        "immediately; the final report is delivered to the session later. "
        "Use for multi-step research, list building, or enrichment."
    )
    parameters: dict = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Required. Natural-language research question or instructions."
                    ),
                },
                "effort": {
                    "type": "string",
                    "description": (
                        "Optional. Research effort. Default follows plugin config."
                    ),
                    "enum": sorted(AGENT_EFFORTS),
                },
                "output_schema": {
                    "type": "object",
                    "description": (
                        "Optional. JSON Schema object for structured output."
                    ),
                },
                "continue_from": {
                    "type": "string",
                    "description": (
                        "Optional. Task id (r-...) of a completed task to "
                        "continue from."
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
        effort: str = "",
        output_schema: object = None,
        continue_from: str = "",
    ) -> str:
        from ..main import (
            PLUGIN_NAME,
            _notification_message_id,
            _notification_scene,
        )
        from .exa_tasks import ConcurrencyLimitError, TaskArchiveError

        plugin = self.plugin
        if plugin is None:
            return "Error: plugin_unavailable: plugin instance not initialized"
        service = plugin._task_service
        if service is None:
            return (
                "Error: agent_unavailable: Agent service not initialized "
                "(check Exa API keys)"
            )
        text = str(query or "").strip()
        if not text:
            return "Error: invalid_request: query must be a non-empty string"
        try:
            outcome = await service.create_task(
                text,
                notification_session=event.unified_msg_origin,
                notification_scene=_notification_scene(event),
                notification_message_id=_notification_message_id(event),
                effort=str(effort or "").strip(),
                output_schema=output_schema,
                previous_task_id=str(continue_from or "").strip(),
            )
        except ConcurrencyLimitError as exc:
            return f"Error: concurrency_limit: {exc}"
        except TaskArchiveError as exc:
            return f"Error: task_not_found: {exc}"
        except ValueError as exc:
            return f"Error: invalid_request: {exc}"
        except RuntimeError as exc:
            return f"Error: agent_error: {exc}"
        except Exception as exc:
            from astrbot.api import logger

            logger.error(f"[{PLUGIN_NAME}] exa-agent-search exception: {exc}")
            return f"Error: internal: {exc}"
        payload = {
            "task_id": outcome.task.task_id,
            "status": outcome.task.status,
            "query": outcome.task.query,
        }
        if outcome.warning:
            payload["warning"] = outcome.warning
        return json.dumps(payload, ensure_ascii=False)
