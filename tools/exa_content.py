MIN_MAX_AGE_HOURS = -1
MAX_MAX_AGE_HOURS = 720


def normalize_max_age_hours(value: object) -> int | None:
    """Normalize Exa content freshness in hours."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError("max_age_hours 必须是 -1 到 720 的整数")
    try:
        hours = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_age_hours 必须是 -1 到 720 的整数") from exc
    if not MIN_MAX_AGE_HOURS <= hours <= MAX_MAX_AGE_HOURS:
        raise ValueError("max_age_hours 必须是 -1 到 720 的整数")
    return hours


def build_contents_payload(
    url: str,
    *,
    max_characters: int = 3000,
    max_age_hours: int | None = None,
) -> dict:
    """Build a Contents API payload with optional freshness control."""
    payload: dict = {
        "ids": [url],
        "text": {"maxCharacters": max_characters},
    }
    normalized_age = normalize_max_age_hours(max_age_hours)
    if normalized_age is not None:
        payload["maxAgeHours"] = normalized_age
    return payload
