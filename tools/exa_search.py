import json
import re

SEARCH_TYPES = frozenset(
    {"lite", "instant", "fast", "auto", "deep-lite", "deep", "deep-reasoning"}
)
SEARCH_CATEGORIES = frozenset(
    {"company", "publication", "news", "personal site", "financial report", "people"}
)

_LEGACY_SEARCH_TYPE_ALIASES = {"keyword": "auto", "neural": "auto"}
_LEGACY_CATEGORY_ALIASES = {"research paper": "publication"}

MAX_OBJECTIVE_LENGTH = 4096
_USER_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

MIN_TIMEOUT_SECONDS = 30
SEARCH_TYPE_MIN_TIMEOUTS = {
    "deep-lite": 30,
    "deep": 60,
    "deep-reasoning": 90,
}


def normalize_search_type(value: object) -> str:
    """Normalize current and legacy Exa search types."""
    search_type = str(value or "").strip().lower()
    search_type = _LEGACY_SEARCH_TYPE_ALIASES.get(search_type, search_type)
    return search_type if search_type in SEARCH_TYPES else "auto"


def normalize_category(value: object) -> str:
    """Normalize current and legacy Exa search categories."""
    category = str(value or "").strip().lower()
    category = _LEGACY_CATEGORY_ALIASES.get(category, category)
    return category if category in SEARCH_CATEGORIES else ""


def resolve_timeout_seconds(timeout_seconds: object, search_type: str = "auto") -> int:
    """Resolve a safe request timeout for the selected search type."""
    try:
        timeout = int(timeout_seconds)
    except (TypeError, ValueError):
        timeout = MIN_TIMEOUT_SECONDS
    return max(
        timeout,
        MIN_TIMEOUT_SECONDS,
        SEARCH_TYPE_MIN_TIMEOUTS.get(search_type, 0),
    )


def _split_domains(value: str) -> list[str]:
    return [domain.strip() for domain in value.split(",") if domain.strip()]


def normalize_user_location(value: object) -> str:
    """Normalize an optional ISO two-letter user location."""
    location = str(value or "").strip().upper()
    return (
        location
        if len(location) == 2 and location.isascii() and location.isalpha()
        else ""
    )


def get_result_snippet(result: dict) -> str:
    """Return highlights when available, otherwise text."""
    highlights = result.get("highlights")
    if isinstance(highlights, list):
        snippets = [str(item) for item in highlights if item]
        if snippets:
            return "\n".join(snippets)
    return str(result.get("text") or "")


def validate_search_filters(
    category: str,
    *,
    exclude_domains: str = "",
    start_published_date: str = "",
    end_published_date: str = "",
) -> None:
    """Reject search filters unsupported by Exa vertical categories."""
    unsupported = []
    if category == "people" and exclude_domains:
        unsupported.append("excludeDomains")
    if category in {"company", "people"}:
        if start_published_date:
            unsupported.append("startPublishedDate")
        if end_published_date:
            unsupported.append("endPublishedDate")
    if unsupported:
        names = ", ".join(unsupported)
        raise ValueError(f'Exa category "{category}" 不支持参数: {names}')


def _add_optional_string(payload: dict, key: str, value: str) -> None:
    if value:
        payload[key] = value


def normalize_objective(value: object) -> str:
    """Normalize an optional search objective; Exa caps it at 4096 chars."""
    objective = str(value or "").strip()
    if not objective:
        return ""
    if len(objective) > MAX_OBJECTIVE_LENGTH:
        raise ValueError(f"objective 不能超过 {MAX_OBJECTIVE_LENGTH} 个字符")
    return objective


def normalize_system_prompt(value: object) -> str:
    """Normalize an optional system prompt guiding output or behavior."""
    return str(value or "").strip()


def normalize_user_identifier(value: object) -> str:
    """Normalize an opaque end-user identifier (1-64 [A-Za-z0-9_-])."""
    identifier = str(value or "").strip()
    if not identifier:
        return ""
    if not _USER_IDENTIFIER_RE.match(identifier):
        raise ValueError("user_identifier 仅支持 1-64 位字母、数字、下划线和连字符")
    return identifier


def normalize_output_schema(value: object) -> dict:
    """Validate a search outputSchema; root type must be text or object."""
    schema = value
    if isinstance(schema, str) and schema.strip():
        try:
            schema = json.loads(schema)
        except ValueError as exc:
            raise ValueError("output_schema 必须是合法的 JSON Schema 对象") from exc
    if not isinstance(schema, dict):
        raise ValueError("output_schema 必须是 JSON 对象")
    root_type = str(schema.get("type") or "")
    if root_type not in {"text", "object"}:
        raise ValueError('output_schema 根类型必须是 "text" 或 "object"')
    return schema


def build_search_payload(
    query: str,
    *,
    num_results: int = 10,
    search_type: str = "auto",
    category: str = "",
    include_domains: str = "",
    exclude_domains: str = "",
    start_published_date: str = "",
    end_published_date: str = "",
    user_location: str = "",
    moderation: bool = False,
    objective: str = "",
    system_prompt: str = "",
    output_schema: object = None,
    user_identifier: str = "",
) -> dict:
    """Build and validate a current Exa search request payload."""
    normalized_type = normalize_search_type(search_type)
    normalized_category = normalize_category(category)
    user_location = normalize_user_location(user_location)
    objective = normalize_objective(objective)
    system_prompt = normalize_system_prompt(system_prompt)
    user_identifier = normalize_user_identifier(user_identifier)
    output_schema = (
        normalize_output_schema(output_schema) if output_schema is not None else None
    )
    include_domains = str(include_domains or "").strip()
    exclude_domains = str(exclude_domains or "").strip()
    start_published_date = str(start_published_date or "").strip()
    end_published_date = str(end_published_date or "").strip()

    validate_search_filters(
        normalized_category,
        exclude_domains=exclude_domains,
        start_published_date=start_published_date,
        end_published_date=end_published_date,
    )
    if normalized_type == "lite":
        unsupported = [
            name
            for name, present in (
                ("outputSchema", output_schema is not None),
                ("systemPrompt", bool(system_prompt)),
            )
            if present
        ]
        if unsupported:
            names = ", ".join(unsupported)
            raise ValueError(f'Exa search type "lite" 不支持参数: {names}。可改用 auto 类型')

    payload: dict = {
        "query": query,
        "numResults": num_results,
        "type": normalized_type,
        "contents": {"highlights": True},
    }
    if user_location:
        payload["userLocation"] = user_location
    if moderation:
        payload["moderation"] = True
    if normalized_category:
        payload["category"] = normalized_category
    if include_domains:
        payload["includeDomains"] = _split_domains(include_domains)
    if exclude_domains:
        payload["excludeDomains"] = _split_domains(exclude_domains)
    _add_optional_string(payload, "startPublishedDate", start_published_date)
    _add_optional_string(payload, "endPublishedDate", end_published_date)
    _add_optional_string(payload, "objective", objective)
    _add_optional_string(payload, "systemPrompt", system_prompt)
    _add_optional_string(payload, "userIdentifier", user_identifier)
    if output_schema is not None:
        payload["outputSchema"] = output_schema
    return payload
