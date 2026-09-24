import asyncio
import importlib
import inspect
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


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


if __name__ == "__main__":
    unittest.main()
