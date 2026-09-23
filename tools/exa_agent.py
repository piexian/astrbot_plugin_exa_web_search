"""HTTP client and normalization for the Exa Agent API."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
REMOTE_STATUSES = frozenset({"queued", "running", "completed", "failed", "cancelled"})
_COST_FIELDS = (
    "total",
    "agentCompute",
    "search",
    "emails",
    "phoneNumbers",
    "dataSources",
)
_SOURCE_FIELDS = ("url", "title", "snippet", "field", "confidence")


class ExaAgentAPIError(RuntimeError):
    """Exa Agent API error with optional unknown-create-outcome state."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 0,
        code: str = "",
        request_id: str = "",
        outcome_uncertain: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id
        self.outcome_uncertain = outcome_uncertain


@dataclass(slots=True)
class RemoteAgentRun:
    run_id: str
    status: str
    stop_reason: str | None = None
    created_at: str = ""
    completed_at: str | None = None
    request_id: str = ""
    text: str = ""
    structured: Any = None
    sources: list[dict[str, Any]] = field(default_factory=list)
    cost: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def result(self) -> dict[str, Any]:
        result: dict[str, Any] = {"text": self.text}
        if self.structured is not None:
            result["structured"] = self.structured
        if self.stop_reason:
            result["stop_reason"] = self.stop_reason
        return result


def redact_secret(value: Any, secret: str) -> str:
    text = str(value or "")
    if secret:
        text = text.replace(secret, "***")
    return text


def _error_fields(
    data: Any, headers: dict[str, str] | None = None
) -> tuple[str, str, str]:
    header_map = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    if not isinstance(data, dict):
        return "", "", header_map.get("x-request-id", "")
    error = data.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or error.get("detail") or error)
        code = str(error.get("code") or error.get("type") or "")
    else:
        message = str(error or data.get("message") or data.get("detail") or "")
        code = str(data.get("code") or "")
    request_id = str(
        data.get("requestId")
        or data.get("request_id")
        or header_map.get("x-request-id", "")
    )
    return message, code, request_id


def _normalize_cost(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    cost: dict[str, Any] = {}
    for field_name in _COST_FIELDS:
        item = value.get(field_name)
        if isinstance(item, (int, float)) and not isinstance(item, bool):
            cost[field_name] = item
        elif isinstance(item, dict):
            nested: dict[str, Any] = {}
            for nested_name, nested_value in item.items():
                if isinstance(nested_value, (int, float)) and not isinstance(
                    nested_value, bool
                ):
                    nested[nested_name] = nested_value
            if nested:
                cost[field_name] = nested
    return cost


def _source_item(value: dict[str, Any], **extra: Any) -> dict[str, Any] | None:
    item = {key: value.get(key) for key in _SOURCE_FIELDS if value.get(key)}
    item.update({key: val for key, val in extra.items() if val})
    return item or None


def normalize_sources(output: Any) -> list[dict[str, Any]]:
    """Flatten Exa grounding citations into a stable archive shape."""
    if not isinstance(output, dict):
        return []
    grounding = output.get("grounding")
    if not isinstance(grounding, list):
        return []
    sources: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for entry in grounding:
        if not isinstance(entry, dict):
            continue
        citations = entry.get("citations")
        if not isinstance(citations, list):
            continue
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            item = _source_item(
                citation,
                field=str(entry.get("field") or ""),
                confidence=str(entry.get("confidence") or ""),
            )
            if not item or not item.get("url"):
                continue
            key = (str(item["url"]), str(item.get("field") or ""))
            if key in seen:
                continue
            seen.add(key)
            sources.append(item)
    return sources


def _error_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("message") or value.get("detail") or value)
    return str(value or "")


def normalize_remote_run(data: Any) -> RemoteAgentRun:
    """Normalize an official AgentRun response without retaining raw headers."""
    if not isinstance(data, dict):
        raise ValueError("Exa Agent Run 响应不是 JSON 对象。")
    run_id = str(data.get("id") or data.get("run_id") or "")
    status = str(data.get("status") or "").lower()
    if not run_id:
        raise ValueError("Exa Agent Run 响应缺少 id。")
    if status not in REMOTE_STATUSES:
        raise ValueError(f"Exa Agent Run 返回未知状态: {status or '<empty>'}")
    output = data.get("output")
    if isinstance(output, str):
        text = output
        structured = None
    elif isinstance(output, dict):
        text = str(output.get("text") or "")
        structured = output.get("structured")
    else:
        text = ""
        structured = None
    error = _error_text(data.get("error"))
    if status == "failed" and not error:
        error = str(data.get("stopReason") or "Exa Agent Run failed")
    if status == "cancelled" and not error:
        error = "Exa Agent Run cancelled"
    return RemoteAgentRun(
        run_id=run_id,
        status=status,
        stop_reason=str(data.get("stopReason")) if data.get("stopReason") else None,
        created_at=str(data.get("createdAt") or ""),
        completed_at=(
            str(data.get("completedAt")) if data.get("completedAt") else None
        ),
        request_id=str(data.get("requestId") or data.get("request_id") or ""),
        text=text,
        structured=structured,
        sources=normalize_sources(output),
        cost=_normalize_cost(data.get("costDollars")),
        error=error,
    )


def _collect_event_sources(value: Any, output: list[dict[str, Any]]) -> None:
    if isinstance(value, dict):
        if value.get("url"):
            item = _source_item(value)
            if item:
                output.append(item)
        for nested in value.values():
            _collect_event_sources(nested, output)
    elif isinstance(value, list):
        for nested in value:
            _collect_event_sources(nested, output)


def sources_from_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract unique source-like URLs from stored Agent events."""
    sources: list[dict[str, Any]] = []
    for event in events:
        event_name = str(event.get("event") or event.get("type") or "").lower()
        if "source" not in event_name and "citation" not in event_name:
            continue
        _collect_event_sources(event.get("data"), sources)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sources:
        url = str(item.get("url") or "")
        if url and url not in seen:
            seen.add(url)
            unique.append(item)
    return unique


class ExaAgentClient:
    """Minimal JSON-first Agent API client."""

    def __init__(
        self,
        session: Any,
        base_url: str,
        *,
        proxy: str | None = None,
        timeout_seconds: int = 60,
    ) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.proxy = proxy
        self.timeout_seconds = timeout_seconds

    async def create_run(
        self,
        query: str,
        api_key: str,
        *,
        effort: str = "auto",
        budget_max_dollars: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RemoteAgentRun:
        payload: dict[str, Any] = {"query": query, "effort": effort}
        if metadata:
            payload["metadata"] = metadata
        if budget_max_dollars is not None and effort in {"auto", "max"}:
            payload["budget"] = {"maxCostDollars": budget_max_dollars}
        data = await self._request(
            "POST",
            "/agent/runs",
            api_key,
            json_payload=payload,
            accept="application/json",
            uncertain_on_server_error=True,
        )
        try:
            return normalize_remote_run(data)
        except (TypeError, ValueError) as exc:
            raise ExaAgentAPIError(
                "Exa Agent API 返回的创建结果无法确认。",
                status=200,
                outcome_uncertain=True,
            ) from exc

    async def get_run(self, run_id: str, api_key: str) -> RemoteAgentRun:
        data = await self._request(
            "GET", f"/agent/runs/{quote(run_id, safe='')}", api_key
        )
        return normalize_remote_run(data)

    async def list_runs(
        self,
        api_key: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        params = {"limit": max(1, min(int(limit), 100))}
        if cursor:
            params["cursor"] = cursor
        data = await self._request("GET", "/agent/runs", api_key, params=params)
        return data if isinstance(data, dict) else {}

    async def list_events(
        self,
        run_id: str,
        api_key: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        params = {"limit": max(1, min(int(limit), 100))}
        if cursor:
            params["cursor"] = cursor
        return await self._request(
            "GET",
            f"/agent/runs/{quote(run_id, safe='')}/events",
            api_key,
            params=params,
        )

    async def list_all_events(
        self,
        run_id: str,
        api_key: str,
        *,
        max_pages: int = 10,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max(1, int(max_pages))):
            data = await self.list_events(run_id, api_key, cursor=cursor)
            if not isinstance(data, dict):
                break
            page = data.get("data")
            if isinstance(page, list):
                events.extend(item for item in page if isinstance(item, dict))
            next_cursor = data.get("nextCursor")
            if not data.get("hasMore") or not next_cursor or next_cursor == cursor:
                break
            cursor = str(next_cursor)
        return events

    async def cancel_run(self, run_id: str, api_key: str) -> RemoteAgentRun:
        data = await self._request(
            "POST",
            f"/agent/runs/{quote(run_id, safe='')}/cancel",
            api_key,
            uncertain_on_server_error=True,
        )
        return normalize_remote_run(data)

    async def _request(
        self,
        method: str,
        endpoint: str,
        api_key: str,
        *,
        json_payload: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        accept: str = "application/json",
        uncertain_on_server_error: bool = False,
    ) -> Any:
        if not api_key:
            raise ValueError("Exa API Key 不能为空。")
        url = f"{self.base_url}{endpoint}"
        headers = {"x-api-key": api_key, "Accept": accept}
        if json_payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            async with self.session.request(
                method,
                url,
                json=json_payload,
                params=params,
                headers=headers,
                timeout=self.timeout_seconds,
                proxy=self.proxy,
            ) as response:
                raw = await response.text()
                status = int(response.status)
                headers_map = dict(getattr(response, "headers", {}) or {})
                if 200 <= status < 300:
                    if not raw.strip():
                        raise ExaAgentAPIError(
                            f"Exa Agent API [{method} {endpoint}] 返回空响应。",
                            status=status,
                            outcome_uncertain=uncertain_on_server_error,
                        )
                    try:
                        return json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise ExaAgentAPIError(
                            f"Exa Agent API [{method} {endpoint}] 返回无效 JSON。",
                            status=status,
                            outcome_uncertain=uncertain_on_server_error,
                        ) from exc
                try:
                    data = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    data = {}
                message, code, request_id = _error_fields(data, headers_map)
                message = redact_secret(message or raw or f"HTTP {status}", api_key)
                raise ExaAgentAPIError(
                    f"Exa Agent API [{method} {endpoint}] HTTP {status}: {message}",
                    status=status,
                    code=code,
                    request_id=request_id,
                    outcome_uncertain=uncertain_on_server_error
                    and (status >= 500 or status == 429),
                )
        except ExaAgentAPIError:
            raise
        except asyncio.TimeoutError as exc:
            raise ExaAgentAPIError(
                f"Exa Agent API [{method} {endpoint}] 请求超时。",
                outcome_uncertain=uncertain_on_server_error,
            ) from exc
        except Exception as exc:
            raise ExaAgentAPIError(
                redact_secret(
                    f"Exa Agent API [{method} {endpoint}] 网络错误: {exc}", api_key
                ),
                outcome_uncertain=uncertain_on_server_error,
            ) from exc
