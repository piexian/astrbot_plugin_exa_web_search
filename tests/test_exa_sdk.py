"""SDK v1 契约回归：公开门面、Main 生命周期与 Agent 归属。"""

import asyncio
import importlib
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.exa_agent import ExaAgentAPIError, RemoteAgentRun
from tools.exa_tasks import TaskNotFoundError
from tools.public_api import ExaPublicService, PluginServiceError, task_snapshot

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = str(PROJECT_ROOT.parent)
MODULE_NAME = "astrbot_plugin_exa_web_search.main"

# 与 main.py 使用同一份模块实例，避免 tools 与包路径双导入产生两套类。
if PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, PACKAGE_ROOT)


def _load_main_module():
    """安装替身并导入 main；模块级符号重绑到当前 main 的模块图。"""
    _install_stubs()
    module = importlib.import_module(MODULE_NAME)
    public_api = importlib.import_module(
        "astrbot_plugin_exa_web_search.tools.public_api"
    )
    exa_tasks = importlib.import_module("astrbot_plugin_exa_web_search.tools.exa_tasks")
    exa_agent = importlib.import_module("astrbot_plugin_exa_web_search.tools.exa_agent")
    globals().update(
        ExaPublicService=public_api.ExaPublicService,
        PluginServiceError=public_api.PluginServiceError,
        task_snapshot=public_api.task_snapshot,
        TaskNotFoundError=exa_tasks.TaskNotFoundError,
        ExaAgentAPIError=exa_agent.ExaAgentAPIError,
        RemoteAgentRun=exa_agent.RemoteAgentRun,
    )
    return module


SNAPSHOT_FIELDS = {
    "task_id",
    "run_id",
    "status",
    "query",
    "result",
    "sources",
    "cost",
    "error",
    "created_at",
    "updated_at",
    "completed_at",
    "owner_plugin_id",
}


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
            return FakeCommandGroup(name)

        return decorator

    @staticmethod
    def permission_type(_permission, **_kwargs):
        def decorator(obj):
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


class StubClientSession:
    closed = False

    def __init__(self, *_args, **_kwargs):
        pass

    async def close(self):
        self.closed = True


def _install_stubs():
    aiohttp = _module(
        "aiohttp",
        ClientSession=StubClientSession,
        ClientTimeout=type("ClientTimeout", (), {"__init__": lambda self, **_: None}),
        ClientError=type("ClientError", (Exception,), {}),
        ClientConnectionError=type("ClientConnectionError", (ConnectionError,), {}),
    )
    astrbot = _module("astrbot")
    astrbot.__path__ = []
    api = _module("astrbot.api", FunctionTool=FakeFunctionTool, logger=FakeLogger())
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
    sys.modules.update(
        {
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
    )
    if PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, PACKAGE_ROOT)


def tearDownModule():
    for name in list(sys.modules):
        if name == MODULE_NAME or name.startswith("astrbot_plugin_exa_web_search"):
            sys.modules.pop(name, None)
    if PACKAGE_ROOT in sys.path:
        sys.path.remove(PACKAGE_ROOT)


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
    """可计数的 Agent API 替身；hold_get 用于让轮询停在在途状态。"""

    def __init__(self):
        self.create_responses = deque()
        self.get_responses = deque()
        self.list_responses = deque()
        self.cancel_response = None
        self.hold_get = None
        self.create_calls = []
        self.get_calls = []
        self.list_calls = []
        self.cancel_calls = []
        self.last_task_id = ""

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
        if self.hold_get is not None:
            await self.hold_get.wait()
        result = (
            self.get_responses.popleft()
            if self.get_responses
            else remote("completed", run_id, text="done")
        )
        if isinstance(result, Exception):
            raise result
        return result

    async def list_runs(self, api_key, **_kwargs):
        self.list_calls.append(api_key)
        result = (
            self.list_responses.popleft()
            if self.list_responses
            else {"data": [], "hasMore": False, "nextCursor": None}
        )
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


class FakeNotifyEvent:
    def __init__(self, origin="qq_official:GroupMessage:group-1"):
        self.unified_msg_origin = origin
        self.message_obj = SimpleNamespace(
            raw_message=SimpleNamespace(group_openid="group-1", channel_id=None),
            message_id="msg-9",
        )

    def get_platform_name(self):
        return "qq_official"

    def is_private_chat(self):
        return False


class _JsonResponse:
    def __init__(self, status=200, body=None, headers=None):
        self.status = status
        self._body = body or {}
        self.headers = headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        return self._body

    async def text(self):
        return json.dumps(self._body)


class _SearchSession:
    """可计数的 HTTP 替身：记录端点请求并返回固定响应。"""

    def __init__(self, body=None, *, status=200):
        self.body = body if body is not None else {"results": []}
        self.status = status
        self.calls = []

    def post(self, url, *, json=None, headers=None, timeout=None, proxy=None):
        self.calls.append({"url": url, "payload": json, "headers": dict(headers or {})})
        if self.status == 200:
            return _JsonResponse(body=self.body)
        return _JsonResponse(status=self.status, body={"error": "rate limited"})


class ServiceApiVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_main_module()

    def test_same_facade_per_load(self):
        plugin = self.module.ExaWebSearchPlugin(FakeContext(), {})
        self.assertIsInstance(plugin.get_service(), ExaPublicService)
        self.assertIs(plugin.get_service(), plugin.get_service(api_version=1))

    def test_only_real_int_v1_is_accepted(self):
        class DerivedVersion(int):
            pass

        plugin = self.module.ExaWebSearchPlugin(FakeContext(), {})
        for bad in (True, False, 2, 0, "1", 1.0, None, DerivedVersion(1)):
            with self.subTest(bad=bad):
                with self.assertRaises(PluginServiceError) as caught:
                    plugin.get_service(bad)
                self.assertEqual(caught.exception.code, "unsupported_version")


class _SdkTestBase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_main_module()

    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.data_dir = Path(self.temp_dir.name)
        self.context = FakeContext()
        self.plugin = self.module.ExaWebSearchPlugin(
            self.context, {"exa_api_keys": ["key-zero"]}
        )
        self.archive = self.module.TaskArchive(self.data_dir / "tasks.sqlite3", 10)
        await self.archive.initialize()
        self.client = FakeAgentClient()
        self.notifications = []
        self.service = self.module.AgentTaskService(
            archive=self.archive,
            client=self.client,
            api_keys=["key-zero", "key-one"],
            data_dir=self.data_dir,
            poll_interval_seconds=0.001,
            reconcile_retry_delay_seconds=0.001,
            notification_sender=self.capture_notification,
            notification_retry_delays=(0, 0),
        )
        await self.service.start()
        self.plugin._task_service = self.service
        self.facade = self.plugin.get_service(1)

    async def capture_notification(self, task, body):
        self.notifications.append((task.task_id, task.notification_session, body))

    async def asyncTearDown(self):
        if self.client.hold_get is not None:
            self.client.hold_get.set()
        await self.service.shutdown()


class MainLifecycleTests(_SdkTestBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        data_patch = patch.object(
            self.module.StarTools, "get_data_dir", return_value=self.data_dir
        )
        data_patch.start()
        self.addCleanup(data_patch.stop)

    async def test_initialize_ready_and_terminate_closes_facade(self):
        facade = self.facade
        self.plugin._task_service = None
        status = facade.get_status()
        self.assertEqual(status["state"], "initializing")
        self.assertFalse(status["ready"])
        self.assertIsNone(status["reason"])
        self.assertFalse(status["agent_ready"])
        self.assertIs(self.plugin.get_service(1), facade)

        await self.plugin.initialize()
        self.assertIsNotNone(self.plugin._task_service)
        status = facade.get_status()
        self.assertTrue(status["ready"])
        self.assertEqual(status["state"], "ready")
        self.assertIsNone(status["reason"])
        self.assertTrue(status["agent_ready"])
        snapshot = await facade.wait_ready(timeout=1)
        self.assertEqual(snapshot, facade.get_status())

        session = SimpleNamespace(closed=False, close=AsyncMock())
        self.plugin._session = session
        await self.plugin.terminate()
        session.close.assert_awaited_once()

        status = facade.get_status()
        self.assertEqual(status["state"], "closed")
        self.assertEqual(status["reason"], "service_closed")
        self.assertFalse(status["ready"])
        self.assertFalse(status["agent_ready"])
        for coro in (
            facade.search("q"),
            facade.wait_ready(timeout=1),
            facade.agent_create("p", "q"),
        ):
            with self.subTest(coro=type(coro).__qualname__):
                with self.assertRaises(PluginServiceError) as caught:
                    await coro
                self.assertEqual(caught.exception.code, "service_closed")
        # 旧门面不能被重新激活。
        facade.mark_initialized()
        self.assertEqual(facade.get_status()["state"], "closed")

    async def test_new_plugin_instance_gets_fresh_facade(self):
        await self.plugin.initialize()
        old_id = self.facade.instance_id
        await self.plugin.terminate()

        plugin = self.module.ExaWebSearchPlugin(
            FakeContext(), {"exa_api_keys": ["key-zero"]}
        )
        facade = plugin.get_service(1)
        self.assertIsNot(facade, self.facade)
        self.assertNotEqual(facade.instance_id, old_id)
        facade.mark_initialized()
        self.assertTrue(facade.get_status()["ready"])
        await plugin.terminate()

    async def test_agent_initialization_failure_keeps_root_ready(self):
        self.plugin._task_service = None
        with patch.object(
            self.module.StarTools,
            "get_data_dir",
            side_effect=RuntimeError("data dir unavailable"),
        ):
            await self.plugin.initialize()
        self.assertIsNone(self.plugin._task_service)
        status = self.facade.get_status()
        self.assertTrue(status["ready"])
        self.assertFalse(status["agent_ready"])
        with self.assertRaises(PluginServiceError) as caught:
            await self.facade.agent_create("p", "q")
        self.assertEqual(caught.exception.code, "feature_disabled")
        # Agent 未就绪不影响普通搜索。
        self.plugin._get_session = lambda: _SearchSession({"results": [{"url": "x"}]})
        results = await self.facade.search("q")
        self.assertEqual(results, [{"url": "x"}])

    async def test_missing_keys_reports_not_configured(self):
        plugin = self.module.ExaWebSearchPlugin(FakeContext(), {})
        facade = plugin.get_service(1)
        await plugin.initialize()
        status = facade.get_status()
        self.assertEqual(status["state"], "unavailable")
        self.assertEqual(status["reason"], "not_configured")
        self.assertFalse(status["ready"])
        self.assertFalse(status["agent_ready"])
        with self.assertRaises(TimeoutError):
            await facade.wait_ready(timeout=0.2)
        with self.assertRaises(PluginServiceError) as caught:
            await facade.search("q")
        self.assertEqual(caught.exception.code, "not_configured")
        # 配置热更新保留同一门面，并即时反映到状态。
        plugin.config["exa_api_keys"] = ["key-zero"]
        status = facade.get_status()
        self.assertTrue(status["ready"])
        self.assertIsNone(status["reason"])
        self.assertFalse(status["agent_ready"])

    async def test_status_and_capabilities_have_no_side_effects(self):
        self.facade.mark_initialized()

        def _fail(*_args, **_kwargs):
            raise AssertionError("status query must not touch runtime resources")

        self.plugin._get_session = _fail
        self.plugin._task_service = SimpleNamespace(
            archive=SimpleNamespace(capacity_status=AsyncMock(side_effect=_fail))
        )
        for _ in range(3):
            status = self.facade.get_status()
            self.assertTrue(status["ready"])
            self.assertEqual(status["state"], "ready")
            self.assertEqual(status["api_version"], 1)
            self.assertTrue(status["instance_id"])
            self.assertTrue(status["agent_ready"])
            self.assertEqual(
                self.facade.capabilities(),
                {
                    "api_version": 1,
                    "features": [
                        "web.search",
                        "web.fetch",
                        "code.search",
                        "research.agent",
                    ],
                },
            )
        self.plugin._task_service.archive.capacity_status.assert_not_awaited()


class SdkSearchBusinessTests(_SdkTestBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.facade.mark_initialized()

    def _attach_session(self, session):
        self.plugin._get_session = lambda: session

    async def test_search_delegates_to_exa_search_without_retry(self):
        session = _SearchSession({"results": [{"url": "https://a.example"}]})
        self._attach_session(session)
        results = await self.facade.search(
            "weather",
            num_results=3,
            search_type="deep",
            category="news",
            include_domains="a.com, b.com",
            start_published_date="2026-01-01",
            user_location="US",
            moderation=True,
            timeout=42,
        )
        self.assertEqual(results, [{"url": "https://a.example"}])
        self.assertEqual(len(session.calls), 1)
        call = session.calls[0]
        self.assertEqual(call["url"], "https://api.exa.ai/search")
        payload = call["payload"]
        self.assertEqual(payload["numResults"], 3)
        self.assertEqual(payload["type"], "deep")
        self.assertEqual(payload["category"], "news")
        self.assertEqual(payload["includeDomains"], ["a.com", "b.com"])
        self.assertEqual(payload["startPublishedDate"], "2026-01-01")
        self.assertEqual(payload["userLocation"], "US")
        self.assertTrue(payload["moderation"])
        self.assertEqual(call["headers"]["x-api-key"], "key-zero")

    async def test_search_does_not_wrap_command_retry(self):
        session = _SearchSession(status=429)
        self._attach_session(session)
        with self.assertRaises(self.module.ExaAPIError) as caught:
            await self.facade.search("q")
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(len(session.calls), 1)

    async def test_search_rejects_invalid_filters_before_request(self):
        session = _SearchSession()
        self._attach_session(session)
        with self.assertRaises(ValueError):
            await self.facade.search("q", category="people", exclude_domains="x.com")
        self.assertEqual(session.calls, [])

    async def test_fetch_and_code_context_share_business_entry(self):
        session = _SearchSession(
            {"results": [{"id": "https://a.example", "text": "hi"}]}
        )
        self._attach_session(session)
        results = await self.facade.fetch(
            "https://a.example", max_characters=120, max_age_hours=24
        )
        self.assertEqual(results[0]["text"], "hi")
        payload = session.calls[-1]["payload"]
        self.assertEqual(payload["ids"], ["https://a.example"])
        self.assertEqual(payload["text"]["maxCharacters"], 120)
        self.assertEqual(payload["maxAgeHours"], 24)

        session = _SearchSession({"response": "code"})
        self._attach_session(session)
        data = await self.facade.code_context("how to parse yaml", tokens_num=500)
        self.assertEqual(data["response"], "code")
        call = session.calls[-1]
        self.assertEqual(call["url"], "https://api.exa.ai/context")
        self.assertEqual(call["payload"]["tokensNum"], 500)

    async def test_business_calls_rejected_before_initialize(self):
        session = _SearchSession()
        plugin = self.module.ExaWebSearchPlugin(
            FakeContext(), {"exa_api_keys": ["key-zero"]}
        )
        plugin._get_session = lambda: session
        facade = plugin.get_service(1)
        for coro in (
            facade.search("q"),
            facade.fetch("https://a.example"),
            facade.code_context("q"),
        ):
            with self.subTest(coro=type(coro).__qualname__):
                with self.assertRaises(PluginServiceError) as caught:
                    await coro
                self.assertEqual(caught.exception.code, "not_ready")
        self.assertEqual(session.calls, [])


class SdkAgentFacadeTests(_SdkTestBase):
    async def create_task(self, owner="owner-a", query="research", **kwargs):
        self.client.create_responses.append(remote("running", "agent_run_1"))
        return await self.facade.agent_create(owner, query, **kwargs)

    async def test_create_returns_whitelisted_snapshot(self):
        result = await self.create_task()
        self.assertEqual(set(result["task"]), SNAPSHOT_FIELDS)
        self.assertEqual(result["warning"], "")
        task = result["task"]
        self.assertTrue(task["task_id"].startswith("r-"))
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["run_id"], "agent_run_1")
        self.assertEqual(task["owner_plugin_id"], "owner-a")
        self.assertNotIn("key_slot", task)
        self.assertNotIn("key_fingerprint", task)
        self.assertNotIn("notification_session", task)
        # 归属在远端创建请求之前已落盘。
        saved = await self.archive.get_task(task["task_id"])
        self.assertEqual(saved.owner_plugin_id, "owner-a")
        self.assertEqual(len(self.client.create_calls), 1)

    async def test_default_creates_without_notification(self):
        self.client.create_responses.append(remote("completed", text="done"))
        result = await self.facade.agent_create("owner-a", "research")
        task_id = result["task"]["task_id"]
        saved = await self.archive.get_task(task_id)
        self.assertEqual(saved.notification_status, "disabled")
        self.assertEqual(saved.notification_session, "")
        self.assertEqual(saved.status, "completed")
        self.assertEqual(self.notifications, [])

    async def test_notify_requires_valid_event_before_any_charge(self):
        for kwargs in (
            {"notify": True},
            {"notify": True, "event": None},
            {"notify": True, "event": FakeNotifyEvent(origin="  ")},
            {"notify": 1, "event": FakeNotifyEvent()},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(PluginServiceError) as caught:
                    await self.facade.agent_create("owner-a", "research", **kwargs)
                self.assertEqual(caught.exception.code, "invalid_request")
        self.assertEqual(self.client.create_calls, [])

    async def test_notify_with_event_reuses_notification_fields(self):
        result = await self.create_task(
            event=FakeNotifyEvent(),
            notify=True,
        )
        saved = await self.archive.get_task(result["task"]["task_id"])
        self.assertEqual(saved.notification_session, "qq_official:GroupMessage:group-1")
        self.assertEqual(saved.notification_scene, "group")
        self.assertEqual(saved.notification_message_id, "msg-9")
        self.assertEqual(saved.notification_status, "pending")

    async def test_invalid_owner_and_query_rejected_before_charge(self):
        for plugin_id, query in (
            ("", "q"),
            ("   ", "q"),
            (None, "q"),
            (123, "q"),
            ("ok", "   "),
            ("ok", None),
        ):
            with self.subTest(plugin_id=plugin_id, query=query):
                with self.assertRaises(PluginServiceError) as caught:
                    await self.facade.agent_create(plugin_id, query)
                self.assertEqual(caught.exception.code, "invalid_request")
        self.assertEqual(self.client.create_calls, [])

    async def test_get_and_cancel_require_full_task_id_and_ownership(self):
        self.client.hold_get = asyncio.Event()
        result = await self.create_task(owner="owner-a")
        task_id = result["task"]["task_id"]

        with self.assertRaises(PluginServiceError) as caught:
            await self.facade.agent_get("owner-b", task_id)
        self.assertEqual(caught.exception.code, "task_forbidden")
        with self.assertRaises(PluginServiceError) as caught:
            await self.facade.agent_cancel("owner-b", task_id)
        self.assertEqual(caught.exception.code, "task_forbidden")
        self.assertEqual(self.client.cancel_calls, [])

        # 只接受完整本地 task_id，不做前缀匹配。
        with self.assertRaises(TaskNotFoundError):
            await self.facade.agent_get("owner-a", task_id[:11])
        with self.assertRaises(TaskNotFoundError):
            await self.facade.agent_cancel("owner-a", "r-99999999-9999")

    async def test_command_tasks_stay_out_of_sdk_reach(self):
        command_task = await self.archive.reserve_task("manual", 0, 2)
        for action in ("get", "cancel"):
            with self.subTest(action=action):
                method = (
                    self.facade.agent_get
                    if action == "get"
                    else self.facade.agent_cancel
                )
                with self.assertRaises(PluginServiceError) as caught:
                    await method("owner-a", command_task.task_id)
                self.assertEqual(caught.exception.code, "task_forbidden")
        self.assertEqual(self.client.cancel_calls, [])

    async def test_agent_get_refreshes_and_reports_snapshot(self):
        result = await self.create_task(owner="owner-a")
        task_id = result["task"]["task_id"]
        outcome = await self.facade.agent_get("owner-a", task_id)
        self.assertEqual(outcome["remote_error"], "")
        self.assertEqual(set(outcome["task"]), SNAPSHOT_FIELDS)
        self.assertEqual(outcome["task"]["task_id"], task_id)
        self.assertTrue(self.client.get_calls)

    async def test_agent_cancel_checks_ownership_before_network(self):
        self.client.hold_get = asyncio.Event()
        result = await self.create_task(owner="owner-a")
        task_id = result["task"]["task_id"]
        self.client.cancel_response = remote("cancelled", "agent_run_1")
        snapshot = await self.facade.agent_cancel("owner-a", task_id)
        self.assertEqual(set(snapshot), SNAPSHOT_FIELDS)
        self.assertEqual(snapshot["status"], "cancelled")
        self.assertEqual(len(self.client.cancel_calls), 1)

    async def test_agent_list_filters_owner_before_limit(self):
        for index in range(30):
            owner = "owner-a" if index % 2 == 0 else "owner-b"
            await self.archive.reserve_task(
                "shared topic", 0, 1000, owner_plugin_id=owner
            )
        listed = await self.facade.agent_list("owner-a")
        self.assertEqual(len(listed), 15)
        self.assertEqual({item["owner_plugin_id"] for item in listed}, {"owner-a"})
        self.assertTrue(all(set(item) == SNAPSHOT_FIELDS for item in listed))
        limited = await self.facade.agent_list("owner-a", "shared topic", limit=5)
        self.assertEqual(len(limited), 5)
        self.assertEqual({item["owner_plugin_id"] for item in limited}, {"owner-a"})
        # 全局前 20 条混有两个归属，证明 owner 过滤先于 LIMIT。
        global_view = await self.archive.search_tasks("shared topic", 20)
        self.assertEqual(len(global_view), 20)
        self.assertEqual(len({task.owner_plugin_id for task in global_view}), 2)

    async def test_agent_list_validates_arguments(self):
        await self.archive.reserve_task("q", 0, 1000, owner_plugin_id="owner-a")
        for plugin_id, term, limit in (
            ("", "", 20),
            ("owner-a", 5, 20),
            ("owner-a", "", True),
            ("owner-a", "", 0),
            ("owner-a", "", 101),
            ("owner-a", "", "5"),
        ):
            with self.subTest(limit=limit, term=term):
                with self.assertRaises(PluginServiceError) as caught:
                    await self.facade.agent_list(plugin_id, term, limit=limit)
                self.assertEqual(caught.exception.code, "invalid_request")
        listed = await self.facade.agent_list("owner-a", "", limit=1)
        self.assertEqual(len(listed), 1)

    async def test_owners_share_concurrency_limit(self):
        self.service.max_concurrency = 1
        self.client.hold_get = asyncio.Event()
        first = await self.create_task(owner="owner-a")
        self.assertEqual(first["task"]["status"], "running")
        with self.assertRaises(self.module.ConcurrencyLimitError):
            await self.create_task(owner="owner-b", query="second")
        self.client.cancel_response = remote("cancelled", "agent_run_1")
        await self.facade.agent_cancel("owner-a", first["task"]["task_id"])
        second = await self.create_task(owner="owner-b", query="second")
        self.assertTrue(second["task"]["task_id"].startswith("r-"))

    async def test_unknown_create_outcome_keeps_task_and_warns(self):
        self.client.create_responses.append(
            ExaAgentAPIError("timeout", outcome_uncertain=True)
        )
        result = await self.facade.agent_create("owner-a", "research")
        self.assertIn("不会自动重建", result["warning"])
        task_id = result["task"]["task_id"]
        self.assertTrue(task_id.startswith("r-"))
        self.assertEqual(len(self.client.create_calls), 1)
        outcome = await self.facade.agent_get("owner-a", task_id)
        self.assertEqual(outcome["task"]["task_id"], task_id)
        self.assertIn(outcome["task"]["status"], {"queued", "interrupted"})

    async def test_facade_close_rejects_new_agent_requests(self):
        await self.facade.close()
        status = self.facade.get_status()
        self.assertEqual(status["state"], "closed")
        self.assertFalse(status["agent_ready"])
        for coro in (
            self.create_task(),
            self.facade.agent_list("owner-a"),
        ):
            with self.subTest(coro=type(coro).__qualname__):
                with self.assertRaises(PluginServiceError) as caught:
                    await coro
                self.assertEqual(caught.exception.code, "service_closed")


class LegacyArchiveMigrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = _load_main_module()

    async def test_owner_column_added_without_losing_old_records(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        db_path = Path(temp_dir.name) / "legacy.sqlite3"
        with sqlite3.connect(db_path) as connection:
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
                INSERT INTO tasks (
                    task_id, key_slot, status, query, created_at, updated_at
                ) VALUES ('r-20260901-0001', 0, 'completed', 'legacy', 'c', 'u')
                """
            )

        archive = self.module.TaskArchive(db_path, 10)
        await archive.initialize()
        restored = await archive.get_task("r-20260901-0001")
        self.assertEqual(restored.query, "legacy")
        self.assertEqual(restored.status, "completed")
        self.assertEqual(restored.result_text, "")
        self.assertEqual(restored.owner_plugin_id, "")
        self.assertEqual(restored.notification_status, "disabled")

        # 同库继续写入带归属的新任务；指令全局查询与归属查询并存。
        reserved = await archive.reserve_task("new task", 0, 10, owner_plugin_id="p/a")
        self.assertEqual(reserved.owner_plugin_id, "p/a")
        everyone = await archive.search_tasks("")
        self.assertEqual({task.owner_plugin_id for task in everyone}, {"", "p/a"})
        owned = await archive.search_tasks("", owner_plugin_id="p/a")
        self.assertEqual([task.task_id for task in owned], [reserved.task_id])

    async def test_task_snapshot_uses_whitelist(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        archive = self.module.TaskArchive(Path(temp_dir.name) / "db.sqlite3", 10)
        await archive.initialize()
        task = await archive.reserve_task("q", 0, 2, owner_plugin_id="p/a")
        snapshot = task_snapshot(task)
        self.assertEqual(set(snapshot), SNAPSHOT_FIELDS)


if __name__ == "__main__":
    unittest.main()
