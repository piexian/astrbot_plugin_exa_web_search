"""SDK diagnostic safety, notification validation, and shutdown regressions."""

import ast
import asyncio
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from tools.public_api import ExaPublicService, PluginServiceError

ROOT = Path(__file__).resolve().parents[1]


def make_service(raw_keys):
    plugin = SimpleNamespace(config={"exa_api_keys": raw_keys}, _task_service=None)
    service = ExaPublicService(plugin)
    service.mark_initialized()
    return plugin, service


class DiagnosticTests(unittest.TestCase):
    def test_facade_does_not_publish_plugin_or_configuration(self):
        _, service = make_service(["fixture-key"])
        for name in ("plugin", "config", "client", "session"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(service, name))

    def test_empty_or_invalid_keys_report_unavailable(self):
        for value in (None, 123, {}, [], [None], [False], [123], [" "]):
            with self.subTest(value=value):
                _, service = make_service(value)
                status = service.get_status()
                self.assertEqual(status["state"], "unavailable")
                self.assertEqual(status["reason"], "not_configured")

    def test_documentation_uses_discovery_not_provider_imports(self):
        text = (ROOT / "docs/plugin-api.md").read_text(encoding="utf-8")
        blocks = re.findall(r"```python\n(.*?)```", text, re.S)
        self.assertTrue(blocks)
        for block in blocks:
            compile(
                block, "plugin-api.md", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT
            )
            for node in ast.walk(ast.parse(block)):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(
                        (node.module or "").startswith("astrbot_plugin_exa")
                    )


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_query_never_reaches_paid_creation(self):
        plugin, service = make_service(["fixture-key"])
        create = AsyncMock(side_effect=AssertionError("paid creation reached"))
        plugin._task_service = SimpleNamespace(create_task=create)
        for value in (123, True, ["q"], {"query": "q"}):
            with self.subTest(value=value):
                with self.assertRaises(PluginServiceError) as caught:
                    await service.agent_create("owner", value)
                self.assertEqual(caught.exception.code, "invalid_request")
        create.assert_not_awaited()

    async def test_cancelled_close_keeps_old_service_permanently_closed(self):
        plugin, service = make_service(["fixture-key"])
        entered, release = asyncio.Event(), asyncio.Event()

        async def search(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return []

        plugin._exa_search = search
        request = asyncio.create_task(service.search("q"))
        await entered.wait()
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(0)
        self.assertEqual(service.get_status()["state"], "closing")
        closing.cancel()
        try:
            with self.assertRaises(asyncio.CancelledError):
                await closing
            self.assertEqual(service.get_status()["state"], "closed")
            service.mark_initialized()
            self.assertEqual(service.get_status()["state"], "closed")
        finally:
            release.set()
            await request


class MainCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_shutdown_failure_still_closes_http(self):
        from test_exa_sdk import FakeContext, _load_main_module

        module = _load_main_module()
        plugin = module.ExaWebSearchPlugin(FakeContext(), {})
        plugin._task_service = SimpleNamespace(
            shutdown=AsyncMock(side_effect=RuntimeError("shutdown probe"))
        )
        session = SimpleNamespace(closed=False, close=AsyncMock())
        plugin._session = session
        with self.assertRaisesRegex(RuntimeError, "shutdown probe"):
            await plugin.terminate()
        session.close.assert_awaited_once()
        self.assertIsNone(plugin._session)
        self.assertIsNone(plugin._task_service)
        self.assertEqual(plugin.get_service().get_status()["state"], "closed")

    async def test_invalid_notification_origin_is_rejected_before_creation(self):
        from test_exa_sdk import FakeContext, FakeNotifyEvent, _load_main_module

        module = _load_main_module()
        plugin = module.ExaWebSearchPlugin(FakeContext(), {})
        create = AsyncMock(side_effect=AssertionError("paid creation reached"))
        plugin._task_service = SimpleNamespace(create_task=create)
        for origin in ("bad-origin", ":GroupMessage:g", "qq:GroupMessage:"):
            with self.subTest(origin=origin):
                with self.assertRaises(RuntimeError) as caught:
                    await plugin.get_service().agent_create(
                        "owner", "q", notify=True, event=FakeNotifyEvent(origin)
                    )
                self.assertEqual(
                    getattr(caught.exception, "code", None), "invalid_request"
                )
        create.assert_not_awaited()


class OwnerRaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_deleted_owned_task_never_falls_back_to_another_owners_prefix(self):
        import sqlite3
        import tempfile

        from tools.exa_task_service import AgentTaskService
        from tools.exa_tasks import TaskArchive, TaskNotFoundError

        for method in ("agent_get", "agent_cancel"):
            with (
                self.subTest(method=method),
                tempfile.TemporaryDirectory() as directory,
            ):
                archive = TaskArchive(Path(directory) / "tasks.sqlite3")
                client = SimpleNamespace(
                    get_run=AsyncMock(
                        side_effect=AssertionError("cross-owner refresh")
                    ),
                    cancel_run=AsyncMock(
                        side_effect=AssertionError("cross-owner cancel")
                    ),
                )
                runtime = AgentTaskService(
                    archive=archive,
                    client=client,
                    api_keys=["fixture"],
                    data_dir=directory,
                )
                await runtime.start()
                try:
                    a = await archive.reserve_task("a", 0, 2, owner_plugin_id="a")
                    await archive.update_task(a.task_id, status="completed")
                    b = await archive.reserve_task("b", 0, 2, owner_plugin_id="b")
                    await archive.update_task(
                        b.task_id, status="running", run_id="remote-b"
                    )
                    stem = a.task_id.rsplit("-", 1)[0]
                    full_id = stem + "-1000"
                    # Daily sequence 10000 can start with the complete ID for 1000.
                    with sqlite3.connect(archive.db_path) as connection:
                        connection.execute(
                            "UPDATE tasks SET task_id=? WHERE task_id=?",
                            (full_id, a.task_id),
                        )
                        connection.execute(
                            "UPDATE tasks SET task_id=? WHERE task_id=?",
                            (full_id + "0", b.task_id),
                        )
                    original_get = archive.get_task

                    async def read_then_cleanup(task_id):
                        task = await original_get(task_id)
                        if task.task_id == full_id:
                            await archive.delete_task(full_id)
                        return task

                    archive.get_task = read_then_cleanup
                    plugin, facade = make_service(["fixture"])
                    plugin._task_service = runtime
                    with self.assertRaises(TaskNotFoundError):
                        await getattr(facade, method)("a", full_id)
                    client.get_run.assert_not_awaited()
                    client.cancel_run.assert_not_awaited()
                finally:
                    await runtime.shutdown()
