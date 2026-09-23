import asyncio
import tempfile
import unittest
from collections import deque
from pathlib import Path

from tools.exa_agent import ExaAgentAPIError, RemoteAgentRun
from tools.exa_task_service import (
    AgentTaskService,
    KeySlotMismatchError,
    key_fingerprint,
)
from tools.exa_tasks import TaskArchive


def remote(status="running", run_id="agent_run_1", text="", sources=None, cost=None):
    return RemoteAgentRun(
        run_id=run_id,
        status=status,
        stop_reason="schema_satisfied" if status == "completed" else None,
        created_at="2026-09-24T01:00:00+00:00",
        completed_at="2026-09-24T01:01:00+00:00" if status == "completed" else None,
        request_id="req-1",
        text=text,
        structured=None,
        sources=sources or [],
        cost=cost or {},
    )


class FakeAgentClient:
    def __init__(self):
        self.create_responses = deque()
        self.get_responses = deque()
        self.list_responses = deque()
        self.cancel_response = None
        self.create_calls = []
        self.get_calls = []
        self.list_calls = []
        self.last_task_id = ""
        self.cancel_calls = []

    async def create_run(self, query, api_key, **_kwargs):
        self.create_calls.append((query, api_key, _kwargs))
        metadata = _kwargs.get("metadata") or {}
        self.last_task_id = str(metadata.get("task_id") or "")
        result = self.create_responses.popleft()
        if isinstance(result, Exception):
            raise result
        return result

    async def get_run(self, run_id, api_key):
        self.get_calls.append((run_id, api_key))
        if self.get_responses:
            result = self.get_responses.popleft()
        else:
            result = remote("completed", run_id, text="done")
        if isinstance(result, Exception):
            raise result
        return result

    async def list_runs(self, api_key, **_kwargs):
        self.list_calls.append(api_key)
        if self.list_responses:
            result = self.list_responses.popleft()
        else:
            result = {"data": [], "hasMore": False, "nextCursor": None}
        if callable(result):
            result = result(self)
        if isinstance(result, Exception):
            raise result
        return result

    async def list_all_events(self, _run_id, _api_key, **_kwargs):
        return []

    async def cancel_run(self, run_id, api_key):
        self.cancel_calls.append((run_id, api_key))
        if isinstance(self.cancel_response, Exception):
            raise self.cancel_response
        return self.cancel_response


class ExaTaskServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        self.archive = TaskArchive(data_dir / "tasks.sqlite3", max_size_mb=10)
        self.client = FakeAgentClient()
        self.service = AgentTaskService(
            archive=self.archive,
            client=self.client,
            api_keys=["key-zero", "key-one"],
            data_dir=data_dir,
            max_concurrency=2,
            poll_interval_seconds=0.001,
            status_retry_limit=2,
        )
        await self.service.start()

    async def asyncTearDown(self):
        await self.service.shutdown()
        self.temp_dir.cleanup()

    async def wait_for_status(self, task_id, status):
        for _ in range(200):
            task = await self.archive.get_task(task_id)
            if task.status == status:
                return task
            await asyncio.sleep(0.001)
        self.fail(f"task did not reach {status}")

    async def test_create_and_poll_reuse_the_same_key_slot(self):
        self.client.create_responses.append(remote("running"))
        self.client.get_responses.append(remote("completed", text="done", cost={"total": 2}))

        outcome = await self.service.create_task("compare frameworks")

        self.assertEqual(outcome.task.key_slot, 0)
        await self.wait_for_status(outcome.task.task_id, "completed")
        self.assertEqual(self.client.create_calls[0][1], "key-zero")
        self.assertTrue(self.client.get_calls)
        self.assertTrue(all(key == "key-zero" for _, key in self.client.get_calls))
        db_bytes = (Path(self.temp_dir.name) / "tasks.sqlite3").read_bytes()
        self.assertNotIn(b"key-zero", db_bytes)
        self.assertNotIn(b"key-one", db_bytes)

    async def test_uncertain_create_reconciles_metadata_without_recreating(self):
        self.client.create_responses.append(
            ExaAgentAPIError("timeout", outcome_uncertain=True)
        )
        self.client.list_responses.append(
            lambda client: {
                "data": [
                    {
                        "id": "agent_run_recovered",
                        "status": "completed",
                        "completedAt": "2026-09-24T01:02:00Z",
                        "request": {
                            "metadata": {"task_id": client.last_task_id}
                        },
                        "output": {"text": "recovered"},
                    }
                ],
                "hasMore": False,
                "nextCursor": None,
            }
        )

        outcome = await self.service.create_task("research")

        self.assertIn("不会自动重建", outcome.warning)
        task = await self.wait_for_status(outcome.task.task_id, "completed")
        self.assertEqual(task.run_id, "agent_run_recovered")
        self.assertEqual(len(self.client.create_calls), 1)

    async def test_reconciled_running_run_gets_a_real_monitor(self):
        self.client.create_responses.append(
            ExaAgentAPIError("timeout", outcome_uncertain=True)
        )
        self.client.list_responses.append(
            lambda client: {
                "data": [
                    {
                        "id": "agent_run_running",
                        "status": "running",
                        "request": {
                            "metadata": {"task_id": client.last_task_id}
                        },
                        "output": {},
                    }
                ],
                "hasMore": False,
                "nextCursor": None,
            }
        )
        self.client.get_responses.append(
            remote("completed", "agent_run_running", text="polled")
        )
        outcome = await self.service.create_task("research")
        task = await self.wait_for_status(outcome.task.task_id, "completed")
        self.assertEqual(task.result_text, "polled")
        self.assertTrue(self.client.get_calls)

    async def test_restart_recovers_existing_run(self):
        task = await self.archive.reserve_task("resume", 1, 2)
        await self.archive.update_task(
            task.task_id, status="running", run_id="agent_run_existing"
        )
        self.client.get_responses.append(
            remote("completed", "agent_run_existing", text="resumed")
        )

        restarted = AgentTaskService(
            archive=self.archive,
            client=self.client,
            api_keys=["key-zero", "key-one"],
            data_dir=self.temp_dir.name,
            poll_interval_seconds=0.001,
        )
        await restarted.start()
        try:
            restored = await self.wait_for_status(task.task_id, "completed")
            self.assertEqual(restored.result_text, "resumed")
            self.assertEqual(self.client.get_calls[0], ("agent_run_existing", "key-one"))
        finally:
            await restarted.shutdown()

    async def test_cancel_uses_bound_key_and_keeps_cost(self):
        task = await self.archive.reserve_task("cancel", 1, 2)
        await self.archive.update_task(
            task.task_id, status="running", run_id="agent_run_cancel"
        )
        self.client.cancel_response = remote(
            "cancelled", "agent_run_cancel", cost={"total": 0.75}
        )

        cancelled = await self.service.cancel_task(task.task_id)

        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(cancelled.cost["total"], 0.75)
        self.assertEqual(self.client.cancel_calls, [("agent_run_cancel", "key-one")])

    async def test_reordered_keys_fail_closed_without_remote_request(self):
        task = await self.archive.reserve_task(
            "key bound",
            0,
            2,
            key_fingerprint=key_fingerprint("key-zero"),
        )
        await self.archive.update_task(
            task.task_id, status="running", run_id="agent_run_key"
        )
        reordered = AgentTaskService(
            archive=self.archive,
            client=self.client,
            api_keys=["key-one", "key-zero"],
            data_dir=self.temp_dir.name,
            poll_interval_seconds=0.001,
        )
        await reordered.start()
        try:
            with self.assertRaises(KeySlotMismatchError):
                reordered._key_for_task(task)
            self.assertEqual(self.client.get_calls, [])
        finally:
            await reordered.shutdown()

    async def test_missing_remote_run_is_interrupted_and_never_recreated(self):
        task = await self.archive.reserve_task("missing", 0, 2)
        await self.service._reconcile_once(task)

        restored = await self.archive.get_task(task.task_id)
        self.assertEqual(restored.status, "interrupted")
        self.assertEqual(self.client.create_calls, [])


if __name__ == "__main__":
    unittest.main()
