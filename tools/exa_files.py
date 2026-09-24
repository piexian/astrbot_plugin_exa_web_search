"""Rendering helpers for Exa task search, stats, and Markdown exports."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .exa_tasks import CapacityStatus, CleanupResult, TaskRecord


def format_bytes(value: int | float) -> str:
    size = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GiB"


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_duration(task: TaskRecord) -> str:
    created = _parse_time(task.created_at)
    completed = _parse_time(task.completed_at)
    if not created or not completed:
        return "进行中" if task.is_active else "未知"
    seconds = max(0.0, (completed - created).total_seconds())
    if seconds < 60:
        return f"{seconds:.1f} 秒"
    minutes, remainder = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes} 分 {remainder} 秒"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} 小时 {minutes} 分"


def format_cost(cost: dict[str, Any]) -> str:
    total = cost.get("total")
    if isinstance(total, (int, float)) and not isinstance(total, bool):
        return f"${total:.4f}"
    return "暂无"


def _cleanup_text(cleanup: CleanupResult | None) -> str:
    if cleanup is None or not cleanup.deleted_task_ids:
        return "暂无清理记录"
    mode = "自动" if cleanup.automatic else "手动"
    return (
        f"最近{mode}清理：{len(cleanup.deleted_task_ids)} 个任务，"
        f"释放 {format_bytes(cleanup.freed_bytes)}，剩余 "
        f"{format_bytes(cleanup.remaining_bytes)}"
    )


def _capacity_text(capacity: CapacityStatus) -> str:
    return (
        f"归档容量：{format_bytes(capacity.used_bytes)} / "
        f"{format_bytes(capacity.max_bytes)} ({capacity.percent:.1f}%)"
    )


def render_task_list(
    tasks: list[TaskRecord], term: str, capacity: CapacityStatus
) -> str:
    lines = ["Exa Agent 本地任务"]
    if term:
        lines.append(f"筛选：{term}")
    if not tasks:
        lines.append("未找到匹配任务。")
    else:
        for task in tasks:
            lines.append(
                f"- {task.task_id} | {task.status} | {task.created_at} | "
                f"{task.query.replace(chr(10), ' ')[:120]}"
            )
    lines.extend(("", _capacity_text(capacity)))
    warning = capacity.warning
    if warning:
        lines.append(warning)
    return "\n".join(lines)


def render_task_stats(
    task: TaskRecord,
    capacity: CapacityStatus,
    cleanup: CleanupResult | None = None,
    *,
    remote_error: str = "",
) -> str:
    lines = [
        f"Exa Agent 任务 {task.task_id}",
        f"状态：{task.status}",
        f"查询：{task.query}",
        f"创建：{task.created_at}",
        f"完成：{task.completed_at or '-'}",
        f"耗时：{format_duration(task)}",
        f"Exa Run ID：{task.run_id or '-'}",
        f"引用数量：{len(task.sources)}",
        f"费用：{format_cost(task.cost)}",
    ]
    summary = task.result_summary
    if summary:
        lines.extend(("", "结果摘要：", summary[:1500]))
    if task.error:
        lines.extend(("", f"错误：{task.error}"))
    if remote_error:
        lines.extend(("", f"远程状态刷新失败：{remote_error}"))
    lines.extend(("", _capacity_text(capacity), _cleanup_text(cleanup)))
    warning = capacity.warning
    if warning:
        lines.append(warning)
    return "\n".join(lines)


def render_task_markdown(task: TaskRecord) -> str:
    lines = [
        "# Exa Agent 任务归档",
        "",
        f"- 任务号：{task.task_id}",
        f"- 查询内容：{task.query}",
        f"- 创建时间：{task.created_at}",
        f"- 完成时间：{task.completed_at or '-'}",
        f"- Exa Run ID：{task.run_id or '-'}",
        f"- 状态：{task.status}",
        f"- 费用：{format_cost(task.cost)}",
    ]
    lines.extend(("", "## 最终结果", ""))
    if task.result_text:
        lines.append(task.result_text)
    elif task.result.get("structured") is not None:
        lines.append(
            "```json\n"
            + json.dumps(
                task.result["structured"], ensure_ascii=False, indent=2, default=str
            )
            + "\n```"
        )
    else:
        lines.append("暂无最终结果。")
    lines.extend(("", "## 引用来源", ""))
    if task.sources:
        for index, source in enumerate(task.sources, 1):
            title = str(source.get("title") or source.get("url") or "来源")
            url = str(source.get("url") or "")
            field = str(source.get("field") or "")
            suffix = f"（字段：{field}）" if field else ""
            lines.append(f"{index}. [{title}]({url}){suffix}")
    else:
        lines.append("暂无引用来源。")
    lines.extend(
        (
            "",
            "## 费用明细",
            "",
            "```json\n" + json.dumps(task.cost, ensure_ascii=False, indent=2) + "\n```",
        )
    )
    if task.error:
        lines.extend(("", "## 错误信息", "", task.error))
    return "\n".join(lines).rstrip() + "\n"


def write_task_markdown(data_dir: str | Path, task: TaskRecord) -> Path:
    export_dir = Path(data_dir) / "exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    timestamp = _parse_time(task.completed_at or task.created_at) or datetime.now(
        timezone.utc
    )
    safe_task_id = re.sub(r"[^A-Za-z0-9_.-]", "_", task.task_id)
    filename = f"{safe_task_id}-{timestamp.strftime('%Y%m%d-%H%M%S')}.md"
    path = export_dir / filename
    path.write_text(render_task_markdown(task), encoding="utf-8")
    return path


def cleanup_exports(
    data_dir: str | Path, *, max_age_seconds: int = 86400, now: float | None = None
) -> int:
    export_dir = Path(data_dir) / "exports"
    if not export_dir.is_dir():
        return 0
    current = now if now is not None else datetime.now(timezone.utc).timestamp()
    removed = 0
    for path in export_dir.glob("*.md"):
        try:
            if current - path.stat().st_mtime > max_age_seconds:
                path.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed
