import sqlite3
import tempfile
import unittest
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

    async def test_manual_preview_includes_pending_notification(self):
        task = await self.archive.reserve_task(
            "pending", 0, 1, notification_session="platform:GroupMessage:group"
        )
        await self.archive.update_task(task.task_id, status="completed")

        manual = await self.archive.preview_cleanup()
        automatic = await self.archive.preview_cleanup(automatic=True)
        deleted = await self.archive.cleanup_terminal(automatic=False)

        self.assertEqual(manual.count, 1)
        self.assertEqual(automatic.count, 0)
        self.assertEqual(deleted.deleted_task_ids, (task.task_id,))

    async def test_manual_preview_matches_deletion_for_mixed_notification_states(self):
        terminal_ids = set()
        for state in ("disabled", "pending", "sending", "sent", "failed"):
            session = "" if state == "disabled" else "platform:GroupMessage:group"
            task = await self.archive.reserve_task(
                state, 0, 1, notification_session=session
            )
            await self.archive.update_task(task.task_id, status="completed")
            if state in ("sending", "sent", "failed"):
                await self.archive.claim_notification(task.task_id)
            if state in ("sent", "failed"):
                await self.archive.finish_notification(
                    task.task_id, error="failed" if state == "failed" else ""
                )
            terminal_ids.add(task.task_id)
        active = await self.archive.reserve_task("active", 0, 1)

        manual = await self.archive.preview_cleanup()
        automatic = await self.archive.preview_cleanup(automatic=True)
        deleted = await self.archive.cleanup_terminal(automatic=False)

        self.assertEqual(manual.count, len(terminal_ids))
        self.assertEqual(automatic.count, 3)
        self.assertEqual(set(deleted.deleted_task_ids), terminal_ids)
        self.assertTrue((await self.archive.get_task(active.task_id)).is_active)

    async def test_snapshot_survives_retry_and_is_cleared_on_success(self):
        task = await self.archive.reserve_task(
            "snapshot", 0, 1, notification_session="platform:GroupMessage:group"
        )
        await self.archive.update_task(
            task.task_id, status="completed", result={"text": "archive body"}
        )
        await self.archive.claim_notification(task.task_id)
        snapshot = f"{task.task_id}\n\narchive body"
        await self.archive.begin_notification_text(task.task_id, snapshot)
        await self.archive.advance_notification_text(task.task_id, 10)
        await self.archive.finish_notification(task.task_id, error="retry", retry=True)

        pending = await self.archive.get_task(task.task_id)
        self.assertEqual(pending.notification_text, snapshot)
        self.assertEqual(pending.notification_text_offset, 10)
        await self.archive.claim_notification(task.task_id)
        await self.archive.finish_notification(task.task_id)

        sent = await self.archive.get_task(task.task_id)
        self.assertEqual(sent.notification_status, "sent")
        self.assertEqual(sent.notification_text, "")
        self.assertEqual(sent.notification_text_offset, 0)
        self.assertEqual(sent.result_text, "archive body")

    async def test_cleanup_estimate_counts_retained_snapshot_bytes(self):
        task = await self.archive.reserve_task(
            "snapshot size", 0, 1, notification_session="platform:GroupMessage:group"
        )
        await self.archive.update_task(task.task_id, status="completed")
        await self.archive.claim_notification(task.task_id)
        before = await self.archive.preview_cleanup()
        snapshot = "未送达正文" * 1000
        await self.archive.begin_notification_text(task.task_id, snapshot)
        after = await self.archive.preview_cleanup()

        self.assertEqual(
            after.estimated_bytes - before.estimated_bytes, len(snapshot.encode("utf-8"))
        )
        await self.archive.finish_notification(task.task_id, error="failed")
        failed = await self.archive.get_task(task.task_id)
        self.assertEqual(failed.notification_status, "failed")
        self.assertEqual(failed.notification_text, snapshot)

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
        interrupted = await small_archive.reserve_task("interrupted", 1, 2)
        await small_archive.update_task(
            interrupted.task_id,
            status="interrupted",
            result={"text": "y" * 20_000},
        )
        running = await small_archive.reserve_task("running", 1, 2)
        await small_archive.update_task(running.task_id, status="running")

        capacity = await small_archive.capacity_status()
        self.assertGreater(capacity.percent, 100)
        result = await small_archive.cleanup_terminal(automatic=True)
        self.assertIn(completed.task_id, result.deleted_task_ids)
        self.assertIn(interrupted.task_id, result.deleted_task_ids)
        self.assertNotIn(running.task_id, result.deleted_task_ids)
        self.assertEqual(
            (await small_archive.get_task(running.task_id)).status, "running"
        )

    async def test_notification_outbox_recovers_and_claims_once(self):
        task = await self.archive.reserve_task(
            "notify",
            0,
            1,
            notification_session="qq_official:GroupMessage:group-1",
            notification_scene="group",
            notification_message_id="msg-1",
        )
        await self.archive.update_task(
            task.task_id, status="completed", result={"text": "full result"}
        )
        pending = await self.archive.recover_pending_notifications()
        self.assertEqual([item.task_id for item in pending], [task.task_id])

        first_claim = await self.archive.claim_notification(task.task_id)
        self.assertEqual(first_claim.notification_status, "sending")
        self.assertEqual(first_claim.notification_attempts, 1)
        self.assertIsNone(await self.archive.claim_notification(task.task_id))

        recovered = await self.archive.recover_pending_notifications()
        self.assertEqual(recovered[0].notification_status, "pending")
        second_claim = await self.archive.claim_notification(task.task_id)
        self.assertEqual(second_claim.notification_attempts, 2)
        await self.archive.finish_notification(task.task_id)

        sent = await self.archive.get_task(task.task_id)
        self.assertEqual(sent.notification_status, "sent")
        self.assertEqual(sent.notification_session, "qq_official:GroupMessage:group-1")
        self.assertEqual(sent.notification_scene, "group")
        self.assertEqual(sent.notification_message_id, "msg-1")

    async def test_automatic_cleanup_preserves_pending_notification(self):
        small_archive = TaskArchive(
            Path(self.temp_dir.name) / "pending.sqlite3", max_size_mb=0.001
        )
        await small_archive.initialize()
        task = await small_archive.reserve_task(
            "pending delivery",
            0,
            1,
            notification_session="qq_official:GroupMessage:group-1",
        )
        await small_archive.update_task(
            task.task_id,
            status="completed",
            result={"text": "x" * 20_000},
        )

        result = await small_archive.cleanup_terminal(automatic=True)

        self.assertNotIn(task.task_id, result.deleted_task_ids)
        self.assertEqual(
            (await small_archive.get_task(task.task_id)).notification_status,
            "pending",
        )

    async def test_migration_preserves_existing_notification_delivery_state(self):
        task = await self.archive.reserve_task(
            "v3 notification", 0, 1, notification_session="platform:GroupMessage:group"
        )
        await self.archive.update_task(
            task.task_id, status="completed", result={"text": "original result"}
        )
        await self.archive.claim_notification(task.task_id)
        with sqlite3.connect(self.archive.db_path) as connection:
            connection.execute("ALTER TABLE tasks DROP COLUMN notification_text")
            connection.execute("ALTER TABLE tasks DROP COLUMN notification_text_offset")
            connection.execute("PRAGMA user_version=3")

        migrated = TaskArchive(self.archive.db_path, max_size_mb=10)
        await migrated.initialize()
        restored = await migrated.get_task(task.task_id)
        self.assertEqual(restored.notification_status, "sending")
        self.assertEqual(restored.notification_attempts, 1)
        self.assertEqual(restored.result_text, "original result")
        self.assertEqual(restored.notification_text, "")
        self.assertEqual(restored.notification_text_offset, 0)

    async def test_initialization_migrates_existing_archive_schema(self):
        old_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        with sqlite3.connect(old_path) as connection:
            connection.execute(
                """
                CREATE TABLE tasks (
                    task_id TEXT PRIMARY KEY, run_id TEXT, key_slot INTEGER NOT NULL,
                    key_fingerprint TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
                    query TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, completed_at TEXT,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    request_id TEXT NOT NULL DEFAULT '',
                    cost_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                INSERT INTO tasks (task_id, key_slot, status, query, created_at, updated_at)
                VALUES ('r-20260924-0001', 0, 'completed', 'legacy', 'created', 'updated')
                """
            )

        migrated = TaskArchive(old_path, max_size_mb=10)
        await migrated.initialize()
        restored = await migrated.get_task("r-20260924-0001")

        self.assertEqual(restored.query, "legacy")
        self.assertEqual(restored.notification_status, "disabled")
        self.assertEqual(restored.notification_session, "")
        self.assertEqual(restored.notification_text, "")
        self.assertEqual(restored.notification_text_offset, 0)
        with sqlite3.connect(old_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)


if __name__ == "__main__":
    unittest.main()
