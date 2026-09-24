"""Lifecycle orchestration for archived Exa Agent tasks."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .exa_agent import (
    ExaAgentAPIError,
    ExaAgentClient,
    RemoteAgentRun,
    normalize_remote_run,
    redact_secret,
    sources_from_events,
)
from .exa_files import cleanup_exports, write_task_markdown
from .exa_tasks import (
    ACTIVE_STATUSES,
    TERMINAL_STATUSES,
    CapacityStatus,
    CleanupPreview,
    CleanupResult,
    TaskArchive,
    TaskNotFoundError,
    TaskRecord,
    utc_now_iso,
)


def _astrbot_logger():
    from astrbot.api import logger

    return logger


_MAX_RECONCILE_PAGES = 100
_RECONCILE_MISS_LIMIT = 5
_DEFAULT_RECONCILE_RETRY_DELAY = 10.0
_SOURCE_RECOVERY_ATTEMPTS = 5
_DEFAULT_SOURCE_RECOVERY_DELAY = 10.0


class KeySlotMismatchError(RuntimeError):
    """Raised when a task's bound API key is no longer at its slot."""


def key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(str(api_key).encode("utf-8")).hexdigest()


@dataclass(slots=True, frozen=True)
class TaskCreationOutcome:
    task: TaskRecord
    warning: str = ""


@dataclass(slots=True, frozen=True)
class TaskStatsResult:
    task: TaskRecord
    remote_error: str = ""


class AgentTaskService:
    """Own Agent polling, recovery, cancellation, and archive maintenance."""

    def __init__(
        self,
        *,
        archive: TaskArchive,
        client: ExaAgentClient,
        api_keys: list[str],
        data_dir: str | Path,
        max_concurrency: int = 2,
        poll_interval_seconds: float = 5,
        reconcile_retry_delay_seconds: float = _DEFAULT_RECONCILE_RETRY_DELAY,
        source_recovery_delay_seconds: float = _DEFAULT_SOURCE_RECOVERY_DELAY,
        status_retry_limit: int = 5,
        effort: str = "auto",
        budget_max_dollars: float | None = 5.0,
        archive_event_pages: int = 10,
        notification_sender: (
            Callable[[TaskRecord, str], Awaitable[None]] | None
        ) = None,
        notification_retry_delays: tuple[float, ...] = (1.0, 5.0),
    ) -> None:
        if not api_keys:
            raise ValueError("至少配置一个 Exa API Key 才能使用 Agent 任务。")
        if int(max_concurrency) < 1:
            raise ValueError("agent_max_concurrent 必须大于 0。")
        if float(poll_interval_seconds) <= 0:
            raise ValueError("agent_poll_interval_seconds 必须大于 0。")
        if float(reconcile_retry_delay_seconds) <= 0:
            raise ValueError("reconcile_retry_delay_seconds 必须大于 0。")
        if float(source_recovery_delay_seconds) <= 0:
            raise ValueError("source_recovery_delay_seconds 必须大于 0。")
        self.archive = archive
        self.client = client
        self._api_keys = tuple(str(key) for key in api_keys if str(key).strip())
        if not self._api_keys:
            raise ValueError("至少配置一个有效的 Exa API Key 才能使用 Agent 任务。")
        self.data_dir = Path(data_dir)
        self.max_concurrency = int(max_concurrency)
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.reconcile_retry_delay_seconds = float(reconcile_retry_delay_seconds)
        self.source_recovery_delay_seconds = float(source_recovery_delay_seconds)
        self.status_retry_limit = max(1, int(status_retry_limit))
        self.effort = effort
        self.budget_max_dollars = budget_max_dollars
        self.archive_event_pages = max(1, int(archive_event_pages))
        self._key_index = 0
        self._key_lock = asyncio.Lock()
        self._monitors: dict[str, asyncio.Task[None]] = {}
        self._source_recovery_tasks: dict[str, asyncio.Task[None]] = {}
        self._stopping = False
        self._started = False
        self.notification_sender = notification_sender
        self.notification_retry_delays = tuple(
            max(0.0, float(delay)) for delay in notification_retry_delays
        )
        self._notifications: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        if self._started:
            return
        await self.archive.initialize()
        await self.ensure_capacity()
        await asyncio.to_thread(cleanup_exports, self.data_dir)
        self._started = True
        for task in await self.archive.recover_pending_notifications():
            self._schedule_notification(task)
        for task in await self.archive.list_active_tasks():
            if task.run_id:
                self._schedule_monitor(task.task_id)
            else:
                self._schedule_reconciliation(task.task_id)

    async def shutdown(self) -> None:
        self._stopping = True
        tasks = [
            *self._monitors.values(),
            *self._source_recovery_tasks.values(),
            *self._notifications.values(),
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._monitors.clear()
        self._source_recovery_tasks.clear()
        self._notifications.clear()

    async def create_task(
        self,
        query: str,
        *,
        notification_session: str = "",
        notification_scene: str = "",
        notification_message_id: str = "",
    ) -> TaskCreationOutcome:
        text = str(query or "").strip()
        if not text:
            raise ValueError("Agent 研究问题不能为空。")
        if not self._started:
            raise RuntimeError("Agent 任务服务尚未初始化。")
        await self.ensure_capacity()
        key_slot = await self._next_key_slot()
        api_key = self._key_for_slot(key_slot)
        task = await self.archive.reserve_task(
            text,
            key_slot,
            self.max_concurrency,
            key_fingerprint=key_fingerprint(api_key),
            notification_session=notification_session,
            notification_scene=notification_scene,
            notification_message_id=notification_message_id,
        )
        try:
            remote = await self.client.create_run(
                text,
                api_key,
                effort=self.effort,
                budget_max_dollars=self.budget_max_dollars,
                metadata={"task_id": task.task_id},
            )
        except ExaAgentAPIError as exc:
            error = redact_secret(str(exc), api_key)
            if exc.outcome_uncertain:
                task = await self.archive.update_task(
                    task.task_id,
                    status="queued",
                    error=f"创建结果未知，正在核对：{error}",
                )
                self._schedule_reconciliation(task.task_id)
                warning = (
                    "远端创建结果暂时未知，已保留本地任务并持续核对；"
                    "插件不会自动重建任务。"
                )
            else:
                task = await self.archive.update_task(
                    task.task_id,
                    status="failed",
                    error=error,
                    completed_at=utc_now_iso(),
                )
                warning = "任务创建失败，已写入本地归档。"
            self._schedule_notification(task)
            await self.ensure_capacity()
            return TaskCreationOutcome(task, warning)
        task = await self._apply_remote_run(task.task_id, remote, api_key)
        if task.is_active:
            self._schedule_monitor(task.task_id)
        await self.ensure_capacity()
        return TaskCreationOutcome(task)

    async def search_tasks(self, term: str = "") -> list[TaskRecord]:
        await self.ensure_capacity()
        tasks = await self.archive.search_tasks(term)
        capacity = await self.ensure_capacity()
        if capacity.percent >= 100:
            tasks = await self.archive.search_tasks(term)
        return tasks

    async def get_task(self, task_id: str) -> TaskStatsResult:
        await self.archive.capacity_status()
        task = await self.archive.resolve_task(task_id)
        remote_error = ""
        if task.is_active:
            try:
                task = await self._refresh_active_task(task)
            except TaskNotFoundError:
                raise
            except Exception as exc:
                remote_error = str(exc)
        await self.ensure_capacity()
        return TaskStatsResult(task, remote_error)

    async def cancel_task(self, task_id: str) -> TaskRecord:
        task = await self.archive.resolve_task(task_id)
        if not task.is_active:
            return task
        if not task.run_id:
            remote = await self._find_remote_run(task)
            if remote is None:
                self._schedule_reconciliation(task.task_id)
                return await self.archive.update_task(
                    task.task_id,
                    error="远端 Run 尚未确认，未执行取消；后台将继续核对。",
                )
            task = await self._apply_remote_run(
                task.task_id, remote, self._key_for_task(task)
            )
        if not task.is_active:
            return task
        if not task.run_id:
            raise RuntimeError("远端 Run 尚未确认，无法取消。")
        api_key = self._key_for_task(task)
        try:
            remote = await self.client.cancel_run(task.run_id, api_key)
        except ExaAgentAPIError as exc:
            error = redact_secret(str(exc), api_key)
            if exc.status == 404:
                self._schedule_monitor(task.task_id)
                return await self.archive.update_task(
                    task.task_id,
                    error="取消接口暂时返回 404，任务仍保持活动状态并继续监控。",
                )
            if exc.outcome_uncertain:
                return await self.archive.update_task(task.task_id, error=error)
            raise
        return await self._apply_remote_run(task.task_id, remote, api_key)

    async def delete_task(self, task_id: str) -> TaskRecord:
        task = await self.archive.resolve_task(task_id)
        deleted = await self.archive.delete_task(task.task_id)
        await self.ensure_capacity()
        self._log_cleanup(self.archive.last_cleanup)
        return deleted

    async def preview_cleanup(self) -> CleanupPreview:
        return await self.archive.preview_cleanup(automatic=False)

    async def delete_all_terminal(self) -> CleanupResult:
        result = await self.archive.cleanup_terminal(automatic=False)
        self._log_cleanup(result)
        return result

    async def get_export_task(self, task_id: str) -> tuple[TaskRecord, Path]:
        task = await self.archive.resolve_task(task_id)
        if task.status not in {"completed", "failed"}:
            raise ValueError("stats -q 仅允许导出 completed 或 failed 任务。")
        path = await asyncio.to_thread(write_task_markdown, self.data_dir, task)
        return task, path

    async def capacity_status(self) -> CapacityStatus:
        return await self.ensure_capacity()

    async def last_cleanup(self) -> CleanupResult | None:
        return self.archive.last_cleanup

    async def ensure_capacity(self) -> CapacityStatus:
        for attempt in range(3):
            capacity = await self.archive.capacity_status()
            if capacity.percent < 100:
                return capacity
            try:
                result = await self.archive.cleanup_terminal(automatic=True)
            except sqlite3.OperationalError as exc:
                _astrbot_logger().warning("自动清理任务归档失败，将重试: %s", exc)
                await asyncio.sleep(0.05)
                continue
            self._log_cleanup(result)
        return await self.archive.capacity_status()

    @staticmethod
    def _log_cleanup(result: CleanupResult | None) -> None:
        if result is None or not result.deleted_task_ids:
            return
        _astrbot_logger().info(
            "Exa Agent 归档清理: tasks=%s freed=%s remaining=%s",
            ",".join(result.deleted_task_ids),
            result.freed_bytes,
            result.remaining_bytes,
        )

    async def _next_key_slot(self) -> int:
        async with self._key_lock:
            slot = self._key_index
            self._key_index = (self._key_index + 1) % len(self._api_keys)
            return slot

    def _key_for_slot(self, slot: int) -> str:
        if slot < 0 or slot >= len(self._api_keys):
            raise ValueError(f"任务使用的 Key 槽位 {slot} 已失效。")
        return self._api_keys[slot]

    def _key_for_task(self, task: TaskRecord) -> str:
        api_key = self._key_for_slot(task.key_slot)
        if task.key_fingerprint and not hmac.compare_digest(
            task.key_fingerprint, key_fingerprint(api_key)
        ):
            raise KeySlotMismatchError(
                f"任务 {task.task_id} 的 API Key 槽位已变化；请恢复原 Key 后重启插件。"
            )
        return api_key

    def _schedule_monitor(self, task_id: str) -> None:
        if self._stopping:
            return
        existing = self._monitors.get(task_id)
        current = asyncio.current_task()
        if existing and not existing.done() and existing is not current:
            return
        task = asyncio.create_task(
            self._monitor_task(task_id), name=f"exa-agent-monitor-{task_id}"
        )
        self._monitors[task_id] = task
        task.add_done_callback(lambda done, tid=task_id: self._monitor_done(tid, done))

    def _schedule_reconciliation(self, task_id: str) -> None:
        if self._stopping:
            return
        existing = self._monitors.get(task_id)
        if existing and not existing.done():
            return
        task = asyncio.create_task(
            self._reconcile_missing_run(task_id),
            name=f"exa-agent-reconcile-{task_id}",
        )
        self._monitors[task_id] = task
        task.add_done_callback(lambda done, tid=task_id: self._monitor_done(tid, done))

    def _schedule_notification(self, task: TaskRecord) -> None:
        if (
            self.notification_sender is None
            or self._stopping
            or not task.notification_session
            or task.notification_status != "pending"
            or task.status not in TERMINAL_STATUSES
        ):
            return
        existing = self._notifications.get(task.task_id)
        if existing and not existing.done():
            return
        notification = asyncio.create_task(
            self._notify_task(task.task_id),
            name=f"exa-agent-notification-{task.task_id}",
        )
        self._notifications[task.task_id] = notification
        notification.add_done_callback(
            lambda done, tid=task.task_id: self._notification_done(tid, done)
        )

    async def _notify_task(self, task_id: str) -> None:
        if self.notification_sender is None:
            return
        for attempt in range(len(self.notification_retry_delays) + 1):
            task = await self.archive.claim_notification(task_id)
            if task is None:
                return
            if task.status == "completed":
                body = task.result_text or task.result_summary
                if not body:
                    body = "任务已完成，但没有返回正文。"
            else:
                body = task.error or {
                    "cancelled": "任务已取消。",
                    "interrupted": "任务已中断。",
                }.get(task.status, "任务未能完成。")
            try:
                await self.notification_sender(task, body)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:500]}"
                retry = attempt < len(self.notification_retry_delays)
                await self.archive.finish_notification(
                    task_id, error=error, retry=retry
                )
                if not retry:
                    _astrbot_logger().error(
                        "Exa Agent 完成通知发送失败: task=%s error=%s",
                        task_id,
                        error,
                    )
                    return
                delay = self.notification_retry_delays[attempt]
                _astrbot_logger().warning(
                    "Exa Agent 完成通知发送失败，将重试: task=%s attempt=%s/%s",
                    task_id,
                    attempt + 1,
                    len(self.notification_retry_delays) + 1,
                )
                if delay:
                    await self._sleep_or_stop(delay)
                if self._stopping:
                    return
            else:
                await self.archive.finish_notification(task_id)
                _astrbot_logger().info(
                    "Exa Agent 完成通知已发送: task=%s", task_id
                )
                return

    def _notification_done(self, task_id: str, task: asyncio.Task[None]) -> None:
        if self._notifications.get(task_id) is task:
            self._notifications.pop(task_id, None)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error:
            _astrbot_logger().error(
                "Exa Agent 完成通知任务 %s 异常: %s", task_id, error
            )

    def _schedule_source_recovery(
        self, task_id: str, run_id: str, api_key: str
    ) -> None:
        if self._stopping:
            return
        existing = self._source_recovery_tasks.get(task_id)
        if existing and not existing.done():
            return
        task = asyncio.create_task(
            self._recover_sources(task_id, run_id, api_key),
            name=f"exa-agent-sources-{task_id}",
        )
        self._source_recovery_tasks[task_id] = task
        task.add_done_callback(
            lambda done, tid=task_id: self._source_recovery_done(tid, done)
        )

    async def _recover_sources(self, task_id: str, run_id: str, api_key: str) -> None:
        for attempt in range(_SOURCE_RECOVERY_ATTEMPTS):
            if self._stopping:
                return
            try:
                events = await self.client.list_all_events(
                    run_id,
                    api_key,
                    max_pages=self.archive_event_pages,
                )
                sources = sources_from_events(events)
                if sources:
                    await self.archive.update_task(task_id, sources=sources)
                    return
            except TaskNotFoundError:
                return
            except ExaAgentAPIError as exc:
                _astrbot_logger().warning(
                    "Exa Agent 任务 %s 来源恢复失败，将继续重试: %s",
                    task_id,
                    redact_secret(str(exc), api_key),
                )
            if attempt + 1 < _SOURCE_RECOVERY_ATTEMPTS:
                await self._sleep_or_stop(self.source_recovery_delay_seconds)
        _astrbot_logger().warning("Exa Agent 任务 %s 来源恢复重试已耗尽", task_id)

    def _source_recovery_done(self, task_id: str, task: asyncio.Task[None]) -> None:
        if self._source_recovery_tasks.get(task_id) is task:
            self._source_recovery_tasks.pop(task_id, None)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error:
            _astrbot_logger().error(
                "Exa Agent 来源恢复任务 %s 异常: %s", task_id, error
            )

    def _monitor_done(self, task_id: str, task: asyncio.Task[None]) -> None:
        if self._monitors.get(task_id) is task:
            self._monitors.pop(task_id, None)
        if task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error:
            _astrbot_logger().error("Exa Agent 后台任务 %s 异常: %s", task_id, error)

    async def _monitor_task(self, task_id: str) -> None:
        not_found_count = 0
        failure_count = 0
        while not self._stopping:
            try:
                task = await self.archive.get_task(task_id)
            except TaskNotFoundError:
                return
            if task.status not in ACTIVE_STATUSES:
                return
            if not task.run_id:
                updated = await self._reconcile_once(task)
                if updated.is_active:
                    return
                continue
            try:
                api_key = self._key_for_task(task)
            except (KeySlotMismatchError, ValueError) as exc:
                _astrbot_logger().error(
                    "Exa Agent 任务 %s 暂停轮询: %s", task.task_id, exc
                )
                return
            try:
                remote = await self.client.get_run(task.run_id, api_key)
            except ExaAgentAPIError as exc:
                if exc.status == 404:
                    not_found_count += 1
                    if not_found_count >= 3:
                        task = await self.archive.update_task(
                            task.task_id,
                            status="interrupted",
                            error="远端任务不存在或已过期。",
                            completed_at=utc_now_iso(),
                        )
                        self._schedule_notification(task)
                        return
                else:
                    not_found_count = 0
                    failure_count += 1
                    if failure_count % self.status_retry_limit == 1:
                        _astrbot_logger().warning(
                            "查询 Exa Agent 任务 %s 失败，将继续重试: %s",
                            task.task_id,
                            redact_secret(str(exc), api_key),
                        )
                await asyncio.sleep(self.poll_interval_seconds)
                continue
            not_found_count = 0
            failure_count = 0
            task = await self._apply_remote_run(task.task_id, remote, api_key)
            if not task.is_active:
                return
            await self._sleep_or_stop(self.poll_interval_seconds)

    async def _reconcile_missing_run(self, task_id: str) -> None:
        misses = 0
        while not self._stopping:
            try:
                task = await self.archive.get_task(task_id)
            except TaskNotFoundError:
                return
            if task.status not in ACTIVE_STATUSES or task.run_id:
                if task.run_id:
                    self._schedule_monitor(task.task_id)
                return
            try:
                remote = await self._find_remote_run(task)
                if remote is not None:
                    updated = await self._apply_remote_run(
                        task.task_id, remote, self._key_for_task(task)
                    )
                    if updated.is_active:
                        self._schedule_monitor(task.task_id)
                    return
                misses += 1
                if misses >= _RECONCILE_MISS_LIMIT:
                    await self._mark_reconcile_missing(task)
                    return
            except KeySlotMismatchError as exc:
                _astrbot_logger().error(
                    "核对 Exa Agent 任务 %s 暂停: %s", task.task_id, exc
                )
                return
            except Exception as exc:
                _astrbot_logger().warning(
                    "核对 Exa Agent 任务 %s 失败，将继续重试: %s",
                    task.task_id,
                    exc,
                )
            await self._sleep_or_stop(self.reconcile_retry_delay_seconds)

    async def _reconcile_once(self, task: TaskRecord) -> TaskRecord:
        remote = await self._find_remote_run(task)
        if remote is not None:
            updated = await self._apply_remote_run(
                task.task_id, remote, self._key_for_task(task)
            )
            if updated.is_active:
                self._schedule_monitor(task.task_id)
            return updated
        return await self._mark_reconcile_missing(task)

    async def _find_remote_run(self, task: TaskRecord) -> RemoteAgentRun | None:
        api_key = self._key_for_task(task)
        cursor: str | None = None
        for _ in range(_MAX_RECONCILE_PAGES):
            page = await self.client.list_runs(api_key, cursor=cursor, limit=100)
            runs = page.get("data") if isinstance(page, dict) else []
            if isinstance(runs, list):
                for raw_run in runs:
                    if not isinstance(raw_run, dict):
                        continue
                    if self._remote_task_id(raw_run) != task.task_id:
                        continue
                    return normalize_remote_run(raw_run)
            next_cursor = page.get("nextCursor") if isinstance(page, dict) else None
            if not page.get("hasMore") or not next_cursor or next_cursor == cursor:
                break
            cursor = str(next_cursor)
        return None

    async def _mark_reconcile_missing(self, task: TaskRecord) -> TaskRecord:
        task = await self.archive.update_task(
            task.task_id,
            status="interrupted",
            error="未找到对应的远端 Agent Run；为避免重复扣费不会自动重建。",
            completed_at=utc_now_iso(),
        )
        self._schedule_notification(task)
        return task

    async def _refresh_active_task(self, task: TaskRecord) -> TaskRecord:
        if not task.run_id:
            remote = await self._find_remote_run(task)
            if remote is None:
                self._schedule_reconciliation(task.task_id)
                return await self.archive.update_task(
                    task.task_id,
                    error="远端 Run 尚未确认，状态查询将继续由后台核对。",
                )
            task = await self._apply_remote_run(
                task.task_id, remote, self._key_for_task(task)
            )
            if task.is_active:
                self._schedule_monitor(task.task_id)
            return task
        api_key = self._key_for_task(task)
        try:
            remote = await self.client.get_run(task.run_id, api_key)
        except ExaAgentAPIError as exc:
            if exc.status == 404:
                self._schedule_monitor(task.task_id)
                return await self.archive.update_task(
                    task.task_id,
                    error="远端状态暂时不可见，后台将继续核对。",
                )
            raise
        return await self._apply_remote_run(task.task_id, remote, api_key)

    async def _apply_remote_run(
        self, task_id: str, remote: RemoteAgentRun, api_key: str
    ) -> TaskRecord:
        current = await self.archive.get_task(task_id)
        if current.status in TERMINAL_STATUSES:
            return current
        sources = list(remote.sources)
        source_recovery_needed = remote.is_terminal and not sources
        if source_recovery_needed:
            try:
                events = await self.client.list_all_events(
                    remote.run_id,
                    api_key,
                    max_pages=self.archive_event_pages,
                )
                sources = sources_from_events(events)
            except ExaAgentAPIError as exc:
                _astrbot_logger().debug(
                    "读取 Exa Agent 事件失败 %s: %s",
                    remote.run_id,
                    redact_secret(str(exc), api_key),
                )
        update: dict[str, Any] = {
            "status": remote.status,
            "run_id": remote.run_id,
            "result": remote.result,
            "sources": sources,
            "cost": remote.cost,
            "error": redact_secret(remote.error, api_key),
            "completed_at": (
                remote.completed_at or (utc_now_iso() if remote.is_terminal else None)
            ),
        }
        if remote.request_id:
            update["request_id"] = remote.request_id
        updated = await self.archive.update_task(task_id, **update)
        self._schedule_notification(updated)
        if source_recovery_needed and not sources:
            self._schedule_source_recovery(task_id, remote.run_id, api_key)
        return updated

    async def _sleep_or_stop(self, seconds: float) -> None:
        if self._stopping:
            return
        try:
            await asyncio.wait_for(asyncio.sleep(seconds), timeout=seconds)
        except asyncio.TimeoutError:
            return

    @staticmethod
    def _remote_task_id(data: dict[str, Any]) -> str:
        request = data.get("request")
        metadata = data.get("metadata")
        if not isinstance(metadata, dict) and isinstance(request, dict):
            metadata = request.get("metadata")
        if not isinstance(metadata, dict):
            return ""
        return str(metadata.get("task_id") or "")
