import tempfile
import unittest
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tools.exa_tasks import (
    ActiveTaskError,
    ConcurrencyLimitError,
    TaskArchive,
    TaskNotFoundError,
)


class ExaTaskArchiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "exa_tasks.sqlite3"
        self.archive = TaskArchive(self.db_path, max_size_mb=10)
        await self.archive.initialize()

    async def test_sqlite_connections_are_closed_after_operations(self):
        connections = []
        original_connect = self.archive._connect

        def tracked_connect():
            connection = original_connect()
            connections.append(connection)
            return connection

        self.archive._connect = tracked_connect
        task = await self.archive.reserve_task("close", 0, 1)
        await self.archive.get_task(task.task_id)
        self.assertGreaterEqual(len(connections), 2)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    async def test_create_update_and_restart_reload(self):
        task = await self.archive.reserve_task(
            "compare FastAPI and Django",
            key_slot=1,
            max_concurrency=2,
            now=datetime(2026, 9, 24, tzinfo=timezone.utc),
        )
        self.assertEqual(task.task_id, "r-20260924-0001")
        self.assertEqual(task.status, "queued")

        await self.archive.update_task(
            task.task_id,
            status="completed",
            run_id="agent_run_123",
            result={"text": "done", "structured": {"winner": "FastAPI"}},
            sources=[{"title": "FastAPI", "url": "https://fastapi.tiangolo.com/"}],
            request_id="req-1",
            cost={"total": 1.25},
            completed_at="2026-09-24T01:00:00+00:00",
        )

        restarted = TaskArchive(self.db_path, max_size_mb=10)
        await restarted.initialize()
        restored = await restarted.get_task(task.task_id)
        self.assertEqual(restored.run_id, "agent_run_123")
        self.assertEqual(restored.status, "completed")
        self.assertEqual(restored.result["text"], "done")
        self.assertEqual(restored.cost["total"], 1.25)

    async def test_sequence_does_not_reuse_deleted_task_numbers(self):
        now = datetime(2026, 9, 24, tzinfo=timezone.utc)
        first = await self.archive.reserve_task("one", 0, 2, now=now)
        await self.archive.update_task(first.task_id, status="completed")
        await self.archive.delete_task(first.task_id)
        second = await self.archive.reserve_task("two", 0, 2, now=now)
        self.assertEqual(second.task_id, "r-20260924-0002")

    async def test_search_supports_task_date_status_and_fuzzy_query(self):
        now = datetime(2026, 9, 24, 1, 2, 3, tzinfo=timezone.utc)
        completed = await self.archive.reserve_task("FastAPI benchmark", 0, 3, now=now)
        await self.archive.update_task(completed.task_id, status="completed")
        failed = await self.archive.reserve_task("Django security", 1, 3, now=now)
        await self.archive.update_task(failed.task_id, status="failed", error="failed")

        self.assertEqual(len(await self.archive.search_tasks()), 2)
        self.assertEqual(
            (await self.archive.search_tasks("r-20260924-0001"))[0].task_id,
            completed.task_id,
        )
        self.assertEqual(len(await self.archive.search_tasks("2026-09-24")), 2)
        self.assertEqual(len(await self.archive.search_tasks("20260924")), 2)
        self.assertEqual(
            (await self.archive.search_tasks("FastAPI"))[0].task_id, completed.task_id
        )
        self.assertEqual(
            (await self.archive.search_tasks("completed"))[0].task_id, completed.task_id
        )
        self.assertEqual(await self.archive.search_tasks("%"), [])

        self.assertEqual(
            {task.task_id for task in await self.archive.search_tasks("r-2026")},
            {completed.task_id, failed.task_id},
        )

    async def test_terminal_status_absorbs_late_active_response(self):
        task = await self.archive.reserve_task("terminal", 0, 1)
        await self.archive.update_task(
            task.task_id,
            status="completed",
            result={"text": "final"},
            completed_at="2026-09-24T01:01:00+00:00",
        )
        restored = await self.archive.update_task(
            task.task_id, status="running", result={"text": "stale"}
        )
        self.assertEqual(restored.status, "completed")
        self.assertEqual(restored.result_text, "final")

    async def test_concurrency_limit_is_atomic(self):
        await self.archive.reserve_task("one", 0, 1)
        with self.assertRaises(ConcurrencyLimitError):
            await self.archive.reserve_task("two", 1, 1)
        self.assertEqual(len(await self.archive.list_active_tasks()), 1)

    async def test_active_task_cannot_be_deleted(self):
        task = await self.archive.reserve_task("one", 0, 1)
        with self.assertRaises(ActiveTaskError):
            await self.archive.delete_task(task.task_id)
        await self.archive.update_task(task.task_id, status="completed")
        deleted = await self.archive.delete_task(task.task_id)
        self.assertEqual(deleted.task_id, task.task_id)
        with self.assertRaises(TaskNotFoundError):
            await self.archive.get_task(task.task_id)

    async def test_automatic_cleanup_preserves_active_tasks(self):
        small_archive = TaskArchive(
            Path(self.temp_dir.name) / "small.sqlite3", max_size_mb=0.01
        )
        await small_archive.initialize()
        completed = await small_archive.reserve_task("completed", 0, 2)
        await small_archive.update_task(
            completed.task_id,
            status="completed",
            result={"text": "x" * 20_000},
        )
        running = await small_archive.reserve_task("running", 1, 2)
        await small_archive.update_task(running.task_id, status="running")

        capacity = await small_archive.capacity_status()
        self.assertGreater(capacity.percent, 100)
        result = await small_archive.cleanup_terminal(automatic=True)
        self.assertIn(completed.task_id, result.deleted_task_ids)
        self.assertNotIn(running.task_id, result.deleted_task_ids)
        self.assertEqual(
            (await small_archive.get_task(running.task_id)).status, "running"
        )


if __name__ == "__main__":
    unittest.main()
