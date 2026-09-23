SEARCH_TYPES = frozenset(
    {"instant", "fast", "auto", "deep-lite", "deep", "deep-reasoning"}
)
SEARCH_CATEGORIES = frozenset(
    {"company", "publication", "news", "personal site", "financial report", "people"}
)

_LEGACY_SEARCH_TYPE_ALIASES = {"keyword": "auto", "neural": "auto"}
_LEGACY_CATEGORY_ALIASES = {"research paper": "publication"}

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
) -> dict:
    """Build and validate a current Exa search request payload."""
    normalized_type = normalize_search_type(search_type)
    normalized_category = normalize_category(category)
    user_location = normalize_user_location(user_location)
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
    return payload
