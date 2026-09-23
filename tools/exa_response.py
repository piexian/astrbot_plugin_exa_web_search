def normalize_cost_total(value: object) -> object:
    """Normalize object or scalar Exa cost responses for logging."""
    if isinstance(value, dict):
        return value.get("total", "N/A")
    if value is None:
        return "N/A"
    return value
