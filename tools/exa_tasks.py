"""SQLite archive for Exa Agent tasks."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK_STATUSES = frozenset(
    {"queued", "running", "completed", "failed", "cancelled", "interrupted"}
)
ACTIVE_STATUSES = frozenset({"queued", "running"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
AUTO_CLEANUP_STATUSES = frozenset({"completed", "failed", "cancelled"})
MANUAL_CLEANUP_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
_UNSET = object()
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPACT_DATE_RE = re.compile(r"^\d{8}$")
_TASK_PREFIX_RE = re.compile(r"^r-\d*(?:-|$)", re.IGNORECASE)


class TaskArchiveError(RuntimeError):
    """Base archive error."""


class TaskNotFoundError(TaskArchiveError):
    """Raised when a task does not exist."""


class AmbiguousTaskError(TaskArchiveError):
    """Raised when a task prefix matches multiple rows."""


class ActiveTaskError(TaskArchiveError):
    """Raised when an active task is deleted or cleaned."""


class ConcurrencyLimitError(TaskArchiveError):
    """Raised when the local Agent concurrency limit is reached."""


@dataclass(slots=True)
class TaskRecord:
    task_id: str
    run_id: str | None
    key_slot: int
    status: str
    query: str
    created_at: str
    updated_at: str
    key_fingerprint: str = ""
    completed_at: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    request_id: str = ""
    cost: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def result_text(self) -> str:
        text = self.result.get("text")
        return str(text or "")

    @property
    def result_summary(self) -> str:
        if self.result_text:
            return self.result_text
        structured = self.result.get("structured")
        if structured is not None:
            return json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
        return ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> TaskRecord:
        return cls(
            task_id=row["task_id"],
            run_id=row["run_id"],
            key_slot=row["key_slot"],
            key_fingerprint=(
                row["key_fingerprint"] if "key_fingerprint" in row.keys() else ""
            ),
            status=row["status"],
            query=row["query"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            result=_load_object(row["result_json"], {}),
            sources=_load_list(row["sources_json"]),
            request_id=row["request_id"] or "",
            cost=_load_object(row["cost_json"], {}),
            error=row["error"] or "",
        )


@dataclass(slots=True, frozen=True)
class CapacityStatus:
    used_bytes: int
    max_bytes: int

    @property
    def percent(self) -> float:
        if self.max_bytes <= 0:
            return 0.0
        return self.used_bytes / self.max_bytes * 100

    @property
    def warning(self) -> str:
        if self.percent >= 100:
            return (
                f"任务归档已达 {self.percent:.1f}% "
                f"({self.used_bytes}/{self.max_bytes} 字节)，请手动清理终态任务。"
            )
        if self.percent >= 80:
            return (
                f"任务归档已使用 {self.percent:.1f}% "
                f"({self.used_bytes}/{self.max_bytes} 字节)，超过 100% 时将自动清理终态任务。"
            )
        return ""


@dataclass(slots=True, frozen=True)
class CleanupPreview:
    count: int
    estimated_bytes: int
    capacity: CapacityStatus


@dataclass(slots=True, frozen=True)
class CleanupResult:
    deleted_task_ids: tuple[str, ...]
    freed_bytes: int
    remaining_bytes: int
    automatic: bool
    warning: str = ""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_object(value: str | None, default: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return dict(default)
    return parsed if isinstance(parsed, dict) else dict(default)


def _load_list(value: str | None) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _json_dump(value: Any, default: str) -> str:
    if value is None:
        return default
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _measure_db_files(db_path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm", "-journal"):
        path = Path(f"{db_path}{suffix}")
        try:
            total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


class TaskArchive:
    """Async, WAL-backed task archive with bounded automatic cleanup."""

    def __init__(self, db_path: str | Path, max_size_mb: int | float = 100) -> None:
        if float(max_size_mb) <= 0:
            raise ValueError("task_archive_max_size_mb 必须大于 0。")
        self.db_path = Path(db_path)
        self.max_bytes = int(float(max_size_mb) * 1024 * 1024)
        self._lock = asyncio.Lock()
        self.last_cleanup: CleanupResult | None = None

    async def initialize(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._initialize_sync)

    async def reserve_task(
        self,
        query: str,
        key_slot: int,
        max_concurrency: int,
        *,
        key_fingerprint: str = "",
        now: datetime | None = None,
    ) -> TaskRecord:
        text = str(query or "").strip()
        if not text:
            raise ValueError("Agent 研究问题不能为空。")
        if int(key_slot) < 0:
            raise ValueError("Agent Key 槽位无效。")
        async with self._lock:
            return await asyncio.to_thread(
                self._reserve_task_sync,
                text,
                int(key_slot),
                int(max_concurrency),
                str(key_fingerprint),
                now or datetime.now(timezone.utc),
            )

    async def update_task(
        self,
        task_id: str,
        *,
        status: str | None = None,
        run_id: str | None = _UNSET,
        result: dict[str, Any] | None = _UNSET,
        sources: list[dict[str, Any]] | None = _UNSET,
        request_id: str | None = _UNSET,
        cost: dict[str, Any] | None = _UNSET,
        error: str | None | object = _UNSET,
        completed_at: str | None | object = _UNSET,
        updated_at: str | None = None,
    ) -> TaskRecord:
        if status is not None and status not in TASK_STATUSES:
            raise ValueError(f"不支持的任务状态: {status}")
        async with self._lock:
            return await asyncio.to_thread(
                self._update_task_sync,
                task_id,
                status,
                run_id,
                result,
                sources,
                request_id,
                cost,
                error,
                completed_at,
                updated_at or utc_now_iso(),
            )

    async def get_task(self, task_id: str) -> TaskRecord:
        async with self._lock:
            return await asyncio.to_thread(self._get_task_sync, task_id)

    async def resolve_task(self, task_id: str) -> TaskRecord:
        async with self._lock:
            return await asyncio.to_thread(self._resolve_task_sync, task_id)

    async def search_tasks(self, term: str = "", limit: int = 20) -> list[TaskRecord]:
        async with self._lock:
            return await asyncio.to_thread(self._search_tasks_sync, term, limit)

    async def list_active_tasks(self) -> list[TaskRecord]:
        async with self._lock:
            return await asyncio.to_thread(self._list_active_tasks_sync)

    async def delete_task(self, task_id: str) -> TaskRecord:
        before = await self.capacity_status()
        async with self._lock:
            deleted = await asyncio.to_thread(self._delete_task_sync, task_id)
        after = await self.capacity_status()
        self.last_cleanup = CleanupResult(
            (deleted.task_id,),
            max(0, before.used_bytes - after.used_bytes),
            after.used_bytes,
            automatic=False,
            warning=after.warning,
        )
        return deleted

    async def capacity_status(self) -> CapacityStatus:
        used = await asyncio.to_thread(_measure_db_files, self.db_path)
        return CapacityStatus(used_bytes=used, max_bytes=self.max_bytes)

    async def preview_cleanup(self, *, automatic: bool = False) -> CleanupPreview:
        statuses = AUTO_CLEANUP_STATUSES if automatic else MANUAL_CLEANUP_STATUSES
        async with self._lock:
            count, estimated = await asyncio.to_thread(
                self._cleanup_estimate_sync, statuses
            )
        capacity = await self.capacity_status()
        return CleanupPreview(count, estimated, capacity)

    async def cleanup_terminal(self, *, automatic: bool) -> CleanupResult:
        async with self._lock:
            result = await asyncio.to_thread(self._cleanup_terminal_sync, automatic)
        self.last_cleanup = result
        return result

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize_sync(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    key_slot INTEGER NOT NULL,
                    key_fingerprint TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    query TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    request_id TEXT NOT NULL DEFAULT '',
                    cost_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
            if "key_fingerprint" not in columns:
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN key_fingerprint TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS task_counters (
                    day TEXT PRIMARY KEY,
                    next_value INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_task_id ON tasks(task_id)"
            )
            connection.execute("PRAGMA user_version=2")

    def _reserve_task_sync(
        self,
        query: str,
        key_slot: int,
        max_concurrency: int,
        key_fingerprint: str,
        now: datetime,
    ) -> TaskRecord:
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        timestamp = now.isoformat(timespec="seconds")
        day = now.strftime("%Y%m%d")
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                active = connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE status IN ('queued', 'running')"
                ).fetchone()[0]
                if active >= max_concurrency:
                    raise ConcurrencyLimitError(
                        f"Agent 并发任务已达到上限（{max_concurrency}）。"
                    )
                connection.execute(
                    "INSERT OR IGNORE INTO task_counters(day, next_value) VALUES (?, 0)",
                    (day,),
                )
                connection.execute(
                    "UPDATE task_counters SET next_value = next_value + 1 WHERE day = ?",
                    (day,),
                )
                sequence = connection.execute(
                    "SELECT next_value FROM task_counters WHERE day = ?", (day,)
                ).fetchone()[0]
                task_id = f"r-{day}-{sequence:04d}"
                connection.execute(
                    """
                    INSERT INTO tasks (
                        task_id, run_id, key_slot, key_fingerprint, status, query,
                        created_at, updated_at, completed_at,
                        result_json, sources_json, request_id, cost_json, error
                    ) VALUES (?, NULL, ?, ?, 'queued', ?, ?, ?, NULL, '{}', '[]', '', '{}', '')
                    """,
                    (task_id, key_slot, key_fingerprint, query, timestamp, timestamp),
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return TaskRecord.from_row(row)

    def _update_task_sync(
        self,
        task_id: str,
        status: str | None,
        run_id: str | None | object,
        result: dict[str, Any] | None | object,
        sources: list[dict[str, Any]] | None | object,
        request_id: str | None | object,
        cost: dict[str, Any] | None | object,
        error: str | None | object,
        completed_at: str | None | object,
        updated_at: str,
    ) -> TaskRecord:
        fields: list[str] = ["updated_at = ?"]
        values: list[Any] = [updated_at]
        if status is not None:
            fields.append("status = ?")
            values.append(status)
        if run_id is not _UNSET:
            fields.append("run_id = ?")
            values.append(run_id or None)
        if result is not _UNSET:
            fields.append("result_json = ?")
            values.append(_json_dump(result, "{}"))
        if sources is not _UNSET:
            fields.append("sources_json = ?")
            values.append(_json_dump(sources, "[]"))
        if request_id is not _UNSET:
            fields.append("request_id = ?")
            values.append(request_id or "")
        if cost is not _UNSET:
            fields.append("cost_json = ?")
            values.append(_json_dump(cost, "{}"))
        if error is not _UNSET:
            fields.append("error = ?")
            values.append(str(error or ""))
        if completed_at is not _UNSET:
            fields.append("completed_at = ?")
            values.append(completed_at or None)
        values.append(task_id)
        with closing(self._connect()) as connection:
            current = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if current is None:
                raise TaskNotFoundError(f"任务不存在: {task_id}")
            if status is not None and current["status"] in TERMINAL_STATUSES:
                return TaskRecord.from_row(current)
            cursor = connection.execute(
                f"UPDATE tasks SET {', '.join(fields)} WHERE task_id = ?",  # noqa: S608
                values,
            )
            if cursor.rowcount == 0:
                raise TaskNotFoundError(f"任务不存在: {task_id}")
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return TaskRecord.from_row(row)

    def _get_task_sync(self, task_id: str) -> TaskRecord:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        if row is None:
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        return TaskRecord.from_row(row)

    def _resolve_task_sync(self, task_id: str) -> TaskRecord:
        try:
            return self._get_task_sync(task_id)
        except TaskNotFoundError:
            pass
        pattern = f"{_escape_like(task_id)}%"
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM tasks
                WHERE task_id LIKE ? ESCAPE '\\'
                ORDER BY created_at DESC
                LIMIT 2
                """,
                (pattern,),
            ).fetchall()
        if not rows:
            raise TaskNotFoundError(f"任务不存在: {task_id}")
        if len(rows) > 1:
            raise AmbiguousTaskError(f"任务号前缀匹配多条记录: {task_id}")
        return TaskRecord.from_row(rows[0])

    def _list_active_tasks_sync(self) -> list[TaskRecord]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM tasks WHERE status IN ('queued', 'running')
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [TaskRecord.from_row(row) for row in rows]

    def _search_tasks_sync(self, term: str, limit: int) -> list[TaskRecord]:
        text = str(term or "").strip()
        limit = max(1, min(int(limit), 1000))
        with closing(self._connect()) as connection:
            if not text:
                cursor = connection.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
                )
            elif _TASK_PREFIX_RE.match(text):
                cursor = connection.execute(
                    """
                    SELECT * FROM tasks WHERE task_id LIKE ? ESCAPE '\\'
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (f"{_escape_like(text)}%", limit),
                )
            elif _DATE_RE.match(text) or _COMPACT_DATE_RE.match(text):
                date_text = (
                    f"{text[:4]}-{text[4:6]}-{text[6:8]}"
                    if _COMPACT_DATE_RE.match(text)
                    else text
                )
                cursor = connection.execute(
                    """
                    SELECT * FROM tasks WHERE created_at LIKE ? ESCAPE '\\'
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (f"{_escape_like(date_text)}%", limit),
                )
            elif text.lower() in TASK_STATUSES:
                cursor = connection.execute(
                    """
                    SELECT * FROM tasks WHERE status = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (text.lower(), limit),
                )
            else:
                cursor = connection.execute(
                    """
                    SELECT * FROM tasks WHERE query LIKE ? ESCAPE '\\'
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (f"%{_escape_like(text)}%", limit),
                )
            return [TaskRecord.from_row(row) for row in cursor.fetchall()]

    def _reclaim_sync(self, connection: sqlite3.Connection) -> None:
        for _ in range(2):
            checkpoint = connection.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
            if checkpoint and int(checkpoint[0] or 0):
                raise sqlite3.OperationalError("SQLite WAL checkpoint is busy")
        connection.execute("VACUUM")
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint and int(checkpoint[0] or 0):
            raise sqlite3.OperationalError("SQLite WAL checkpoint is busy")

    def _delete_task_sync(self, task_id: str) -> TaskRecord:
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    raise TaskNotFoundError(f"任务不存在: {task_id}")
                if row["status"] in ACTIVE_STATUSES:
                    raise ActiveTaskError(f"活动任务 {task_id} 不能删除。")
                connection.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            self._reclaim_sync(connection)
        return TaskRecord.from_row(row)

    def _cleanup_estimate_sync(self, statuses: frozenset[str]) -> tuple[int, int]:
        placeholders = ",".join("?" for _ in statuses)
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS task_count,
                       COALESCE(SUM(
                           LENGTH(task_id) + LENGTH(COALESCE(run_id, '')) +
                           LENGTH(status) + LENGTH(query) + LENGTH(created_at) +
                           LENGTH(updated_at) + LENGTH(COALESCE(completed_at, '')) +
                           LENGTH(result_json) + LENGTH(sources_json) +
                           LENGTH(request_id) + LENGTH(cost_json) + LENGTH(error) + 160
                       ), 0) AS estimated_bytes
                FROM tasks WHERE status IN ({placeholders})
                """,  # noqa: S608
                tuple(sorted(statuses)),
            ).fetchone()
        return int(row["task_count"]), int(row["estimated_bytes"])

    def _cleanup_terminal_sync(self, automatic: bool) -> CleanupResult:
        before = _measure_db_files(self.db_path)
        statuses = AUTO_CLEANUP_STATUSES if automatic else MANUAL_CLEANUP_STATUSES
        placeholders = ",".join("?" for _ in statuses)
        target_bytes = int(self.max_bytes * 0.9) if automatic else 0
        deleted: list[str] = []
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    f"""
                    SELECT task_id,
                           LENGTH(task_id) + LENGTH(COALESCE(run_id, '')) +
                           LENGTH(status) + LENGTH(query) + LENGTH(created_at) +
                           LENGTH(updated_at) + LENGTH(COALESCE(completed_at, '')) +
                           LENGTH(result_json) + LENGTH(sources_json) +
                           LENGTH(request_id) + LENGTH(cost_json) + LENGTH(error) + 160
                           AS estimated_bytes
                    FROM tasks WHERE status IN ({placeholders})
                    ORDER BY created_at ASC
                    """,  # noqa: S608
                    tuple(sorted(statuses)),
                ).fetchall()
                projected = before
                for row in rows:
                    if automatic and projected <= target_bytes:
                        break
                    connection.execute(
                        "DELETE FROM tasks WHERE task_id = ?", (row["task_id"],)
                    )
                    deleted.append(row["task_id"])
                    projected -= int(row["estimated_bytes"])
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            self._reclaim_sync(connection)
        after = _measure_db_files(self.db_path)
        warning = CapacityStatus(after, self.max_bytes).warning
        return CleanupResult(
            tuple(deleted),
            max(0, before - after),
            after,
            automatic,
            warning,
        )
