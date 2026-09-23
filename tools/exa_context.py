import json

MIN_CONTEXT_TOKENS = 50
MAX_CONTEXT_TOKENS = 100000
MAX_CONTEXT_QUERY_LENGTH = 2000


def normalize_tokens_num(value: object) -> str | int:
    """Normalize Exa Code Context token limits."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "dynamic"
    if isinstance(value, bool):
        raise ValueError("tokens_num 必须是 dynamic 或 50-100000 的整数")
    if isinstance(value, int):
        tokens = value
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.lower() == "dynamic":
            return "dynamic"
        try:
            tokens = int(normalized)
        except ValueError as exc:
            raise ValueError("tokens_num 必须是 dynamic 或 50-100000 的整数") from exc
    else:
        raise ValueError("tokens_num 必须是 dynamic 或 50-100000 的整数")

    if not MIN_CONTEXT_TOKENS <= tokens <= MAX_CONTEXT_TOKENS:
        raise ValueError("tokens_num 必须是 dynamic 或 50-100000 的整数")
    return tokens


def build_context_payload(query: str, *, tokens_num: str | int = "dynamic") -> dict:
    """Build and validate an Exa Code Context request payload."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query 不能为空")
    normalized_query = query.strip()
    if len(normalized_query) > MAX_CONTEXT_QUERY_LENGTH:
        raise ValueError(f"query 不能超过 {MAX_CONTEXT_QUERY_LENGTH} 个字符")
    return {
        "query": normalized_query,
        "tokensNum": normalize_tokens_num(tokens_num),
    }


def format_context_result(data: dict, fallback_query: str) -> str:
    """Format a Context API response for an LLM tool result."""
    response = data.get("response")
    if not isinstance(response, str) or not response:
        raise ValueError("Exa Code Context 返回为空")
    return json.dumps(
        {
            "requestId": data.get("requestId", ""),
            "query": data.get("query", fallback_query),
            "response": response,
            "resultsCount": data.get("resultsCount"),
            "outputTokens": data.get("outputTokens"),
        },
        ensure_ascii=False,
    )
