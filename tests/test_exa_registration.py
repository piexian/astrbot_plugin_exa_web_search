import asyncio
import importlib
import inspect
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.exa_tasks import CapacityStatus, TaskRecord

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = str(PROJECT_ROOT.parent)
MODULE_NAME = "astrbot_plugin_exa_web_search.main"


class FakeLogger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class FakeStar:
    def __init__(self, context):
        self.context = context


class FakeContext:
    def __init__(self):
        self.tools = []

    def add_llm_tools(self, *tools):
        self.tools.extend(tools)


class FakeStarTools:
    @classmethod
    def get_data_dir(cls, _plugin_name=None):
        return PROJECT_ROOT / ".test-data"


class FakeFunctionTool:
    pass


class FakeMessageChain:
    def __init__(self, chain=None):
        self.chain = list(chain or [])

    def message(self, text):
        self.chain.append(text)
        return self


class FakePlain:
    def __init__(self, text):
        self.text = text


class FakeFile:
    def __init__(self, name, file="", url=""):
        self.name = name
        self.file = file
        self.url = url


class FakeCommandGroup:
    def __init__(self, name, parent=None):
        self.command_name = name
        self.parent = parent
        self.permission_required = False
        self.commands = {}

    def command(self, name, **_kwargs):
        def decorator(func):
            func.__command_name__ = name
            func.__command_parent__ = self.command_name
            self.commands[name] = func
            return func

        return decorator

    def group(self, name, **_kwargs):
        return FakeCommandGroup(name, parent=self)


class FakeFilter:
    PermissionType = types.SimpleNamespace(ADMIN="admin")
    CustomFilter = object

    @staticmethod
    def command(name, **_kwargs):
        def decorator(func):
            func.__command_name__ = name
            return func

        return decorator

    @staticmethod
    def command_group(name, **_kwargs):
        def decorator(func):
            group = FakeCommandGroup(name)
            group.permission_required = getattr(func, "__admin_only__", False)
            return group

        return decorator

    @staticmethod
    def permission_type(permission, **_kwargs):
        def decorator(obj):
            obj.__admin_only__ = permission == "admin"
            return obj

        return decorator

    @staticmethod
    def custom_filter(_filter):
        def decorator(func):
            return func

        return decorator


def _module(name, **attributes):
    value = types.ModuleType(name)
    for key, item in attributes.items():
        setattr(value, key, item)
    return value


def _install_stubs():
    aiohttp = _module(
        "aiohttp",
        ClientSession=type("ClientSession", (), {"closed": False}),
        ClientTimeout=type("ClientTimeout", (), {"__init__": lambda self, **_: None}),
        ClientError=type("ClientError", (Exception,), {}),
        ClientConnectionError=type("ClientConnectionError", (ConnectionError,), {}),
    )
    astrbot = _module("astrbot")
    astrbot.__path__ = []
    api = _module(
        "astrbot.api",
        FunctionTool=FakeFunctionTool,
        logger=FakeLogger(),
    )
    event = _module(
        "astrbot.api.event",
        AstrMessageEvent=object,
        MessageChain=FakeMessageChain,
        filter=FakeFilter(),
    )
    star = _module(
        "astrbot.api.star",
        Context=FakeContext,
        Star=FakeStar,
        StarTools=FakeStarTools,
    )
    components = _module(
        "astrbot.api.message_components",
        File=FakeFile,
        Plain=FakePlain,
    )
    core = _module("astrbot.core")
    core.__path__ = []
    star_pkg = _module("astrbot.core.star")
    star_pkg.__path__ = []
    star_filter = _module("astrbot.core.star.filter")
    star_filter.__path__ = []
    command = _module("astrbot.core.star.filter.command", GreedyStr=str)
    session_waiter = _module(
        "astrbot.core.utils.session_waiter",
        SessionController=object,
        SessionFilter=object,
        session_waiter=lambda *_args, **_kwargs: lambda func: func,
    )

    stubs = {
        "aiohttp": aiohttp,
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event,
        "astrbot.api.star": star,
        "astrbot.api.message_components": components,
        "astrbot.core": core,
        "astrbot.core.star": star_pkg,
        "astrbot.core.star.filter": star_filter,
        "astrbot.core.star.filter.command": command,
        "astrbot.core.utils.session_waiter": session_waiter,
    }
    sys.modules.update(stubs)
    if PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, PACKAGE_ROOT)
    return stubs


def tearDownModule():
    for name in list(sys.modules):
        if name == MODULE_NAME or name.startswith("astrbot_plugin_exa_web_search"):
            sys.modules.pop(name, None)
    if PACKAGE_ROOT in sys.path:
        sys.path.remove(PACKAGE_ROOT)


class FakeController:
    def __init__(self):
        self.stopped = False

    def stop(self):
        self.stopped = True


class FakeEvent:
    unified_msg_origin = "test-origin"

    def __init__(self, message="确认清理", admin=True):
        self.message = message
        self.admin = admin
        self.stopped = False

    def get_sender_id(self):
        return "admin-1"

    def get_message_str(self):
        return self.message

    def is_admin(self):
        return self.admin

    def stop_event(self):
        self.stopped = True

    def plain_result(self, text):
        return SimpleNamespace(text=text)


class FakeCleanupService:
    def __init__(self):
        self.cleanup_calls = 0

    async def delete_all_terminal(self):
        self.cleanup_calls += 1
        return SimpleNamespace(
            deleted_task_ids=("r-20260924-0001",),
            freed_bytes=1024,
        )


class FakePreviewService:
    async def preview_cleanup(self):
        return SimpleNamespace(
            count=1,
            estimated_bytes=10,
            capacity=SimpleNamespace(warning=""),
        )

    async def capacity_status(self):
        return SimpleNamespace(warning="")

    async def last_cleanup(self):
        return None


class FakeStatsService:
    async def get_task(self, task_id):
        task = TaskRecord(
            task_id=task_id,
            run_id="agent_run_1",
            key_slot=0,
            status="completed",
            query="stats route",
            created_at="2026-09-24T01:00:00+00:00",
            updated_at="2026-09-24T01:01:00+00:00",
            completed_at="2026-09-24T01:01:00+00:00",
            result={"text": "done"},
        )
        return SimpleNamespace(task=task, remote_error="")

    async def capacity_status(self):
        return CapacityStatus(0, 100 * 1024 * 1024)

    async def last_cleanup(self):
        return None


class ExaCommandRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _install_stubs()
        cls.module = importlib.import_module(MODULE_NAME)

    def test_admin_group_and_every_subcommand_are_registered(self):
        group = self.module.ExaWebSearchPlugin.exa_admin_group
        self.assertEqual(group.command_name, "exa")
        self.assertTrue(group.permission_required)
        expected = {"-r", "-s", "stats", "clean", "cancel"}
        self.assertEqual(set(group.commands), expected)
        for command in group.commands.values():
            with self.subTest(command=getattr(command, "__command_name__", "")):
                self.assertTrue(getattr(command, "__admin_only__", False))

    def test_public_search_command_remains_unrestricted(self):
        public = self.module.ExaWebSearchPlugin.exa_command
        self.assertEqual(public.__command_name__, "exa")
        self.assertFalse(getattr(public, "__admin_only__", False))

    def test_greedy_parameters_use_framework_compatible_defaults(self):
        public_parameter = inspect.signature(
            self.module.ExaWebSearchPlugin.exa_command
        ).parameters["query"]
        self.assertIs(public_parameter.default, self.module.GreedyStr)
        for name in ("exa_research_command", "exa_stats_command"):
            with self.subTest(name=name):
                parameter = inspect.signature(
                    getattr(self.module.ExaWebSearchPlugin, name)
                ).parameters["query"]
                self.assertIs(parameter.default, inspect.Parameter.empty)


class ExaCleanConfirmationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        _install_stubs()
        cls.module = importlib.import_module(MODULE_NAME)

    async def asyncSetUp(self):
        self.plugin = self.module.ExaWebSearchPlugin(FakeContext(), {})
        self.service = FakeCleanupService()
        self.plugin._task_service = self.service

    async def test_stats_handler_uses_bound_query_argument(self):
        self.plugin._task_service = FakeStatsService()
        command = self.module.ExaWebSearchPlugin.exa_admin_group.commands["stats"]
        result = await anext(
            command(
                self.plugin,
                FakeEvent("exa stats r-20260924-0001"),
                "r-20260924-0001",
            )
        )
        self.assertIn("r-20260924-0001", result.text)
        self.assertIn("stats route", result.text)

    async def test_exact_confirmation_deletes_and_stops_session(self):
        event = FakeEvent("确认清理")
        controller = FakeController()
        await self.plugin._confirm_clean(controller, event)
        self.assertEqual(self.service.cleanup_calls, 1)
        self.assertTrue(controller.stopped)
        self.assertTrue(event.stopped)
        session_id = self.module._admin_session_id(event)
        self.assertIn("已清理", self.plugin._clean_outcomes[session_id])

    async def test_cancel_and_permission_change_do_not_delete(self):
        for message, admin in (("取消", True), ("确认清理", False), ("其他", True)):
            with self.subTest(message=message, admin=admin):
                event = FakeEvent(message, admin=admin)
                controller = FakeController()
                await self.plugin._confirm_clean(controller, event)
                self.assertEqual(self.service.cleanup_calls, 0)
                self.assertTrue(controller.stopped)

    async def test_clean_all_timeout_cancels_without_delete(self):
        service = FakePreviewService()
        self.plugin._task_service = service

        async def timeout(*_args, **_kwargs):
            raise asyncio.TimeoutError

        self.plugin._confirm_clean = timeout
        event = FakeEvent("清理")
        responses = self.plugin.exa_clean_command(event, "all")
        first = await anext(responses)
        second = await anext(responses)
        self.assertIn("确认清理", first.text)
        self.assertIn("超时", second.text)


class ExaCompletionDeliveryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        _install_stubs()
        cls.module = importlib.import_module(MODULE_NAME)

    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_dir = Path(self.temp_dir.name)
        data_patch = patch.object(
            self.module.StarTools, "get_data_dir", return_value=self.data_dir
        )
        data_patch.start()
        self.addCleanup(data_patch.stop)
        self.context = FakeContext()
        self.context.send_message = AsyncMock(return_value=True)
        self.plugin = self.module.ExaWebSearchPlugin(self.context, {})
        self.archive = self.module.TaskArchive(self.data_dir / "tasks.sqlite3", 10)
        await self.archive.initialize()
        self.client = SimpleNamespace(create_run=AsyncMock())
        self.service = self.make_service(self.archive)

    def make_service(self, archive):
        service = self.module.AgentTaskService(
            archive=archive,
            client=self.client,
            api_keys=["mock-key"],
            data_dir=self.data_dir,
            notification_sender=partial(
                self.plugin._send_agent_completion_notification, archive=archive
            ),
            notification_retry_delays=(0, 0),
        )
        self.addAsyncCleanup(service.shutdown)
        return service

    async def completed_task(self, body):
        task = await self.archive.reserve_task(
            "private query",
            0,
            2,
            notification_session="qq_official:GroupMessage:group-1",
            notification_scene="group",
            notification_message_id="original-message",
        )
        return await self.archive.update_task(
            task.task_id, status="completed", result={"text": body}
        )

    def assert_no_exports(self):
        self.assertEqual(list((self.data_dir / "exports").glob("*.md")), [])

    async def test_short_completion_is_only_task_id_and_body(self):
        task = await self.completed_task("完整正文")
        platform = SimpleNamespace(
            meta=lambda: SimpleNamespace(id="qq_official"),
            remember_session_scene=lambda *_: None,
            remember_session_message_id=lambda *_: None,
        )
        self.context.platform_manager = SimpleNamespace(platform_insts=[platform])

        await self.service._notify_task(task.task_id)

        args = self.context.send_message.await_args.args
        self.assertEqual(args[0], task.notification_session)
        self.assertEqual(args[1].chain[0].text, f"{task.task_id}\n\n完整正文")
        self.assertEqual(self.context.send_message.await_count, 1)
        saved = await self.archive.get_task(task.task_id)
        self.assertEqual(saved.notification_status, "sent")
        self.assert_no_exports()

    async def test_text_limit_and_long_file_lifetime(self):
        task = await self.completed_task("placeholder")
        body = "字" * (1500 - len(task.task_id) - 2)
        await self.archive.update_task(task.task_id, result={"text": body})
        await self.service._notify_task(task.task_id)
        component = self.context.send_message.await_args.args[1].chain[0]
        self.assertIsInstance(component, FakePlain)
        self.assert_no_exports()

        body += "字"
        task = await self.completed_task(body)
        paths = []

        async def accept_file(_origin, chain):
            component = chain.chain[0]
            self.assertIsInstance(component, FakeFile)
            path = Path(component.file)
            self.assertEqual(component.name, f"{task.task_id}.md")
            content = path.read_text(encoding="utf-8")
            self.assertEqual(content, f"{task.task_id}\n\n{body}\n")
            paths.append(path)
            return True

        self.context.send_message.side_effect = accept_file
        await self.service._notify_task(task.task_id)
        self.assertEqual(len(paths), 1)
        self.assertFalse(paths[0].exists())
        saved = await self.archive.get_task(task.task_id)
        self.assertEqual(saved.notification_status, "sent")

    async def test_file_rejection_falls_back_to_complete_bounded_text(self):
        body = ("中文正文与链接 https://example.com/article\n\n" * 100) + "最后一段"
        task = await self.completed_task(body)
        delivered = []
        files = []

        async def reject_file(_origin, chain):
            component = chain.chain[0]
            if isinstance(component, FakeFile):
                files.append(component.name)
                self.assertTrue(Path(component.file).exists())
                raise NotImplementedError("files are unsupported")
            delivered.append(component.text)
            return True

        self.context.send_message.side_effect = reject_file
        await self.service._notify_task(task.task_id)

        self.assertEqual(files, [f"{task.task_id}.md"])
        self.assertTrue(all(0 < len(chunk) <= 1500 for chunk in delivered))
        self.assertEqual("".join(delivered), f"{task.task_id}\n\n{body}")
        saved = await self.archive.get_task(task.task_id)
        self.assertEqual(saved.notification_status, "sent")
        self.assertEqual(saved.notification_text_offset, len(saved.notification_text))
        self.assert_no_exports()
        self.client.create_run.assert_not_awaited()

    async def test_text_retries_resume_without_resending_file_or_delivered_chunks(self):
        for fail_at in (0, 1):
            with self.subTest(fail_at=fail_at):
                task = await self.completed_task("结果正文" * 1000)
                delivered = []
                file_calls = []
                failed = False

                async def fail_one_chunk(_origin, chain):
                    nonlocal failed
                    component = chain.chain[0]
                    if isinstance(component, FakeFile):
                        file_calls.append(component.name)
                        raise NotImplementedError("files are unsupported")
                    if len(delivered) == fail_at and not failed:
                        failed = True
                        raise RuntimeError("text send rejected")
                    delivered.append(component.text)
                    return True

                self.context.send_message.side_effect = fail_one_chunk
                await self.service._notify_task(task.task_id)

                saved = await self.archive.get_task(task.task_id)
                self.assertEqual(saved.notification_status, "sent")
                self.assertEqual(saved.notification_attempts, 2)
                self.assertEqual(len(file_calls), 1)
                expected = f"{task.task_id}\n\n{task.result_text}"
                self.assertEqual("".join(delivered), expected)
                self.assert_no_exports()

    async def test_restart_resumes_snapshot_after_partial_delivery(self):
        task = await self.completed_task("原始正文" * 1000)
        delivered = []

        async def interrupt_second_chunk(_origin, chain):
            component = chain.chain[0]
            if isinstance(component, FakeFile):
                raise NotImplementedError("files are unsupported")
            if delivered:
                raise asyncio.CancelledError
            delivered.append(component.text)
            return True

        self.context.send_message.side_effect = interrupt_second_chunk
        with self.assertRaises(asyncio.CancelledError):
            await self.service._notify_task(task.task_id)
        saved = await self.archive.get_task(task.task_id)
        self.assertEqual(saved.notification_status, "sending")
        self.assertEqual(saved.notification_text_offset, len(delivered[0]))
        await self.service.shutdown()
        await self.archive.update_task(task.task_id, result={"text": "later result"})

        async def accept_remaining(_origin, chain):
            self.assertIsInstance(chain.chain[0], FakePlain)
            delivered.append(chain.chain[0].text)
            return True

        self.context.send_message.side_effect = accept_remaining
        restored_archive = self.module.TaskArchive(self.archive.db_path, 10)
        restored_service = self.make_service(restored_archive)
        await restored_service.start()
        await asyncio.gather(*restored_service._notifications.values())

        restored = await restored_archive.get_task(task.task_id)
        self.assertEqual(restored.notification_status, "sent")
        self.assertEqual(restored.notification_attempts, 2)
        self.assertEqual("".join(delivered), f"{task.task_id}\n\n{task.result_text}")
        self.assert_no_exports()

    async def test_file_timeout_retries_file_without_text_fallback(self):
        task = await self.completed_task("长正文" * 1000)
        paths = []

        async def timeout_once(_origin, chain):
            component = chain.chain[0]
            self.assertIsInstance(component, FakeFile)
            path = Path(component.file)
            self.assertTrue(path.exists())
            paths.append(path)
            if len(paths) == 1:
                raise TimeoutError("delivery outcome unknown")
            return True

        self.context.send_message.side_effect = timeout_once
        await self.service._notify_task(task.task_id)
        saved = await self.archive.get_task(task.task_id)
        self.assertEqual(saved.notification_status, "sent")
        self.assertEqual(saved.notification_attempts, 2)
        self.assertEqual(saved.notification_text, "")
        self.assertEqual(len(paths), 2)
        self.assert_no_exports()

    async def test_file_is_removed_when_upload_is_cancelled(self):
        task = await self.completed_task("长正文" * 1000)
        paths = []

        async def cancel_upload(_origin, chain):
            paths.append(Path(chain.chain[0].file))
            self.assertTrue(paths[0].exists())
            raise asyncio.CancelledError

        self.context.send_message.side_effect = cancel_upload
        with self.assertRaises(asyncio.CancelledError):
            await self.service._notify_task(task.task_id)
        self.assertEqual(len(paths), 1)
        self.assert_no_exports()

    async def test_cleanup_failure_does_not_repeat_successful_delivery(self):
        task = await self.completed_task("长正文" * 1000)
        with patch.object(Path, "unlink", side_effect=PermissionError("file is busy")):
            await self.service._notify_task(task.task_id)
        self.assertEqual(self.context.send_message.await_count, 1)
        saved = await self.archive.get_task(task.task_id)
        self.assertEqual(saved.notification_status, "sent")

    async def test_missing_platform_is_not_marked_as_sent(self):
        task = await self.completed_task("short body")
        self.context.send_message.return_value = False
        await self.service._notify_task(task.task_id)
        saved = await self.archive.get_task(task.task_id)
        self.assertEqual(saved.notification_status, "failed")

    async def test_ordinary_stats_does_not_send_long_result_file(self):
        task = await self.completed_task("长正文" * 1000)
        self.plugin._task_service = SimpleNamespace(
            get_task=AsyncMock(return_value=SimpleNamespace(task=task, remote_error="")),
            get_export_task=AsyncMock(),
            capacity_status=AsyncMock(return_value=CapacityStatus(0, 1000)),
            last_cleanup=AsyncMock(return_value=None),
        )
        event = FakeEvent(f"exa stats {task.task_id}")
        event.send = AsyncMock()
        command = self.module.ExaWebSearchPlugin.exa_admin_group.commands["stats"]
        results = [item async for item in command(self.plugin, event, task.task_id)]
        self.assertEqual(len(results), 1)
        self.assertIn("仅显示前 1500", results[0].text)
        event.send.assert_not_awaited()
        self.context.send_message.assert_not_awaited()
        self.plugin._task_service.get_export_task.assert_not_awaited()
        self.assert_no_exports()


class ExaRequestSafetyTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        _install_stubs()
        cls.module = importlib.import_module(MODULE_NAME)

    async def test_invalid_base_url_fails_before_tool_registration(self):
        for base_url in ("", "  ", "not-a-url", "https://api.exa.ai/search"):
            with self.subTest(base_url=base_url):
                context = FakeContext()
                with self.assertRaises(ValueError):
                    self.module.ExaWebSearchPlugin(
                        context, {"exa_base_url": base_url, "exa_api_keys": ["key"]}
                    )
                self.assertEqual(context.tools, [])

    async def test_missing_base_url_uses_explicit_default(self):
        plugin = self.module.ExaWebSearchPlugin(FakeContext(), {})
        self.assertEqual(plugin._base_url, "https://api.exa.ai")

    async def test_retry_after_header_is_passed_through_and_bounded(self):
        class RateLimitedResponse:
            status = 429

            def __init__(self, header):
                self.headers = {"Retry-After": header}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def text(self):
                return '{"error": "rate limited"}'

        class FakeSession:
            def __init__(self, header):
                self.response = RateLimitedResponse(header)

            def post(self, *_args, **_kwargs):
                return self.response

        plugin = self.module.ExaWebSearchPlugin(
            FakeContext(), {"exa_api_keys": ["key"]}
        )
        for header, expected in (
            ("17", 17),
            ("9999", 300),
            ("9" * 5000, 300),
            ("00017", 17),
            ("invalid", None),
        ):
            with self.subTest(header=header):
                plugin._get_session = lambda h=header: FakeSession(h)
                with self.assertRaises(self.module.ExaAPIError) as caught:
                    await plugin._exa_request("/search", {"query": "test"})
                self.assertEqual(caught.exception.retry_after, expected)

    async def test_retry_after_http_date_and_fallback(self):
        future = datetime.now(timezone.utc) + timedelta(seconds=60)
        http_date = format_datetime(future, usegmt=True)
        self.assertLessEqual(self.module._parse_retry_after(http_date), 60)
        self.assertGreater(self.module._parse_retry_after(http_date), 0)
        self.assertEqual(self.module._parse_retry_after("0"), 0)
        self.assertIsNone(self.module._parse_retry_after("nope"))

        plugin = self.module.ExaWebSearchPlugin(
            FakeContext(), {"exa_api_keys": ["key"], "max_retries": 1, "retry_delay": 2}
        )
        for status, retry_after, expected in (
            (429, 17, 17),
            (429, None, 2),
            (500, 17, 2),
        ):
            with self.subTest(retry_after=retry_after):
                plugin._exa_search = AsyncMock(
                    side_effect=[
                        self.module.ExaAPIError(
                            "rate limited", status=status, retry_after=retry_after
                        ),
                        [],
                    ]
                )
                with patch.object(
                    self.module.asyncio, "sleep", new_callable=AsyncMock
                ) as sleeper:
                    result = await plugin._search_with_retry("test")
                self.assertTrue(result["ok"])
                sleeper.assert_awaited_once_with(expected)


if __name__ == "__main__":
    unittest.main()
