"""Pure parsers for Exa command routing."""

from __future__ import annotations

ADMIN_ROUTE_PREFIXES = frozenset({"-r", "-s", "stats", "clean", "cancel"})


def _single_token(value: str, message: str) -> str:
    text = str(value or "").strip()
    if not text or len(text.split()) != 1:
        raise ValueError(message)
    return text


def extract_exa_payload(message: str) -> str:
    """Extract the raw payload after the /exa command prefix."""
    text = str(message or "").strip()
    if text.startswith("/exa"):
        text = text[1:].lstrip()
    if text == "exa":
        return ""
    if text.startswith("exa "):
        return text[3:].lstrip()
    return text


def parse_search_term(value: str) -> str:
    """Parse an optional -s prefix without splitting the search term."""
    text = str(value or "").strip()
    if text == "-s" or text.startswith("-s "):
        text = text[2:].strip()
    return text


def is_reserved_exa_query(value: str) -> bool:
    """Return whether a public /exa payload belongs to an admin route."""
    text = str(value or "").strip()
    if not text:
        return False
    return text.split(maxsplit=1)[0] in ADMIN_ROUTE_PREFIXES


def parse_research_query(value: str) -> str:
    """Parse an optional -r prefix without changing inner spacing."""
    text = str(value or "").strip()
    if text == "-r" or text.startswith("-r "):
        text = text[2:].strip()
    if not text:
        raise ValueError("研究问题不能为空。")
    return text


def parse_stats_query(value: str) -> tuple[bool, str]:
    """Parse `task_id` or `-q task_id` and return (export, task_id)."""
    text = str(value or "").strip()
    if text == "-q" or text.startswith("-q "):
        return True, _single_token(text[2:], "stats -q 后必须提供任务号。")
    return False, _single_token(text, "stats 后必须提供任务号。")


def parse_clean_query(value: str) -> str:
    """Parse a task id or the `all` cleanup target."""
    text = _single_token(str(value or ""), "clean 后必须提供任务号或 all。")
    return "all" if text.lower() == "all" else text
