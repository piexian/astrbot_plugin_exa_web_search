"""SDK v1 公开门面：把 Exa 搜索与 Agent 任务暴露给其他插件。

发现方式由消费者通过 context.get_registered_star 调用 Main.get_service 完成；
门面只暴露具名方法，不透传插件实例、HTTP 客户端或配置。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from copy import deepcopy
from functools import wraps
from typing import TYPE_CHECKING, Any

from .exa_tasks import TaskNotFoundError

if TYPE_CHECKING:
    from ..main import ExaWebSearchPlugin
    from .exa_task_service import AgentTaskService

MAX_OWNER_LENGTH = 128
_MAX_TASK_ID_LENGTH = 128

__all__ = [
    "ExaPublicService",
    "PluginServiceError",
    "TaskNotFoundError",
    "task_snapshot",
]


class PluginServiceError(RuntimeError):
    """SDK v1 服务错误；code 为稳定错误码。"""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _submission(function):
    @wraps(function)
    async def call(self, *args, **kwargs):
        completed = asyncio.Event()
        self._submissions.add(completed)
        try:
            return await function(self, *args, **kwargs)
        finally:
            self._submissions.discard(completed)
            completed.set()

    return call


def _owner_id(value: object) -> str:
    if not isinstance(value, str):
        raise PluginServiceError("invalid_request", "plugin_id 必须是字符串")
    owner = value.strip()
    if not owner or len(owner) > MAX_OWNER_LENGTH or any(ord(c) < 32 for c in owner):
        raise PluginServiceError(
            "invalid_request", "plugin_id 不能为空、过长或含控制字符"
        )
    return owner


def _task_id(value: object) -> str:
    if not isinstance(value, str):
        raise PluginServiceError("invalid_request", "task_id 必须是字符串")
    text = value.strip()
    if not text or len(text) > _MAX_TASK_ID_LENGTH or any(c.isspace() for c in text):
        raise PluginServiceError("invalid_request", "请提供完整的本地任务号 task_id")
    return text


def task_snapshot(task) -> dict[str, Any]:
    """把 TaskRecord 转成白名单快照，不包含 Key 槽位、指纹、通知会话或路径。"""
    return {
        "task_id": task.task_id,
        "run_id": task.run_id or "",
        "status": task.status,
        "query": task.query,
        "result": deepcopy(task.result),
        "sources": deepcopy(task.sources),
        "cost": deepcopy(task.cost),
        "error": task.error,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "completed_at": task.completed_at,
        "owner_plugin_id": task.owner_plugin_id,
    }


class ExaPublicService:
    """同进程公开服务；同次插件加载返回同一实例。"""

    api_version = 1

    # SDK v1 固定能力标识：声明实现支持的能力，不代表账号权限或当前可执行。
    features = ("web.search", "web.fetch", "code.search", "research.agent")

    def __init__(self, plugin: ExaWebSearchPlugin):
        self._plugin = plugin
        self.instance_id = uuid.uuid4().hex
        self._initialized = False
        self._closing = False
        self._closed = False
        self._submissions: set[asyncio.Event] = set()

    # ---- 生命周期与状态 ------------------------------------------------

    def for_api_version(self, api_version: int = 1) -> ExaPublicService:
        """按 SDK 约定协商版本并返回当前服务；只接受真正的 int 1。"""
        if type(api_version) is not int or api_version != 1:
            raise PluginServiceError(
                "unsupported_version", "仅支持 Exa 插件服务接口 v1"
            )
        return self

    def mark_initialized(self) -> None:
        """Main.initialize 结束后调用；已关闭的旧门面不会被重新激活。"""
        if not self._closed:
            self._initialized = True

    async def close(self) -> None:
        """第一时间拒绝新请求，等待在途请求结束后永久关闭。"""
        self._closing = True
        try:
            await asyncio.gather(*(event.wait() for event in list(self._submissions)))
        finally:
            self._closed = True

    def _has_api_keys(self) -> bool:
        raw = self._plugin.config.get("exa_api_keys", [])
        keys = [raw] if isinstance(raw, str) else raw
        if not isinstance(keys, (list, tuple)):
            return False
        return any(isinstance(key, str) and key.strip() for key in keys)

    def get_status(self) -> dict[str, Any]:
        """本地、无网络与文件副作用的快照；关闭后仍可查询。"""
        state, reason = "initializing", None
        if self._closed:
            state, reason = "closed", "service_closed"
        elif self._closing:
            state, reason = "closing", "service_closed"
        elif self._initialized:
            if self._has_api_keys():
                state = "ready"
            else:
                state, reason = "unavailable", "not_configured"
        return {
            "api_version": self.api_version,
            "instance_id": self.instance_id,
            "state": state,
            "ready": state == "ready",
            "reason": reason,
            "agent_ready": self._agent_ready(),
        }

    def _agent_ready(self) -> bool:
        """根搜索就绪不依赖 Agent 初始化成功，两者在此显式分离。"""
        if self._closing or self._closed:
            return False
        return self._plugin._task_service is not None

    def capabilities(self) -> dict[str, Any]:
        return {"api_version": self.api_version, "features": list(self.features)}

    async def wait_ready(self, timeout: float | None = None) -> dict[str, Any]:
        """等待根服务就绪；成功返回 get_status 同形快照。"""
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while True:
            status = self.get_status()
            if status["ready"]:
                return status
            if status["state"] in {"closing", "closed"}:
                raise PluginServiceError("service_closed", "服务实例已关闭，请重新获取")
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("wait_ready 超时：Exa 插件服务仍未就绪")
            await asyncio.sleep(0.1 if remaining is None else min(0.1, remaining))

    def _require_ready(self) -> None:
        status = self.get_status()
        if status["state"] in {"closing", "closed"}:
            raise PluginServiceError("service_closed", "服务实例已关闭，请重新获取")
        if not status["ready"]:
            raise PluginServiceError(
                status["reason"] or "not_ready",
                f"Exa 搜索服务暂不可用（{status['state']}）",
            )

    def _require_agent(self) -> AgentTaskService:
        if self._closing or self._closed:
            raise PluginServiceError("service_closed", "服务实例已关闭，请重新获取")
        service = self._plugin._task_service
        if service is None:
            raise PluginServiceError(
                "feature_disabled", "Exa Agent 任务功能未初始化，请检查插件配置和日志"
            )
        return service

    # ---- 搜索业务 ------------------------------------------------------

    @_submission
    async def search(
        self,
        query: str,
        *,
        num_results: int = 10,
        search_type: str = "auto",
        category: str = "",
        include_domains: str = "",
        exclude_domains: str = "",
        start_published_date: str = "",
        end_published_date: str = "",
        user_location: str = "",
        moderation: bool | None = None,
        timeout: int | None = None,
    ) -> list[dict[str, Any]]:
        """联网搜索；完整委托 _exa_search，无指令自动重试，返回底层结果列表。"""
        self._require_ready()
        return await self._plugin._exa_search(
            query,
            num_results=num_results,
            search_type=search_type,
            category=category,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            start_published_date=start_published_date,
            end_published_date=end_published_date,
            user_location=user_location,
            moderation=moderation,
            timeout=timeout,
        )

    @_submission
    async def fetch(
        self,
        url: str,
        *,
        max_characters: int = 3000,
        max_age_hours: int | None = None,
        timeout: int | None = None,
    ) -> list[dict[str, Any]]:
        """提取网页内容；委托 _exa_extract，返回底层结果列表。"""
        self._require_ready()
        return await self._plugin._exa_extract(
            url,
            max_characters=max_characters,
            max_age_hours=max_age_hours,
            timeout=timeout,
        )

    @_submission
    async def code_context(
        self,
        query: str,
        *,
        tokens_num: str | int = "dynamic",
        timeout: int | None = None,
    ) -> dict[str, Any]:
        """获取代码上下文；委托 _exa_code_context，返回业务字典。"""
        self._require_ready()
        return await self._plugin._exa_code_context(
            query, tokens_num=tokens_num, timeout=timeout
        )

    # ---- Agent 任务 ----------------------------------------------------

    @staticmethod
    def _notification_fields(event) -> tuple[str, str, str]:
        from ..main import (
            AstrMessageEvent,
            _notification_message_id,
            _notification_scene,
        )

        origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
        parts = origin.split(":", 2)
        if not isinstance(event, AstrMessageEvent) or len(parts) != 3 or not all(parts):
            raise PluginServiceError(
                "invalid_request", "notify=True 时必须传入有效的 AstrMessageEvent"
            )
        return origin, _notification_scene(event), _notification_message_id(event)

    @_submission
    async def agent_create(
        self,
        plugin_id: str,
        query: str,
        *,
        event=None,
        notify: bool = False,
    ) -> dict[str, Any]:
        """创建 Agent 任务；归属在本地预约时落盘，先于任何收费远端请求。

        默认不发通知；notify=True 时必须传入有效 AstrMessageEvent。
        返回 {"task": 快照, "warning": 提示}；创建结果未知时 task 仍携带
        本地 task_id，由插件后台核对，不会自动重建。
        """
        owner = _owner_id(plugin_id)
        if not isinstance(query, str) or not query.strip():
            raise PluginServiceError("invalid_request", "query 必须是非空字符串")
        text = query.strip()
        if type(notify) is not bool:
            raise PluginServiceError("invalid_request", "notify 必须是布尔值")
        if notify:
            session, scene, message_id = self._notification_fields(event)
        else:
            session = scene = message_id = ""
        service = self._require_agent()
        outcome = await service.create_task(
            text,
            owner_plugin_id=owner,
            notification_session=session,
            notification_scene=scene,
            notification_message_id=message_id,
        )
        return {"task": task_snapshot(outcome.task), "warning": outcome.warning}

    async def _owned_task(self, service: AgentTaskService, owner: str, task_id: str):
        """先按完整本地 task_id 精确查询，再校验归属；不做前缀匹配。"""
        task = await service.archive.get_task(_task_id(task_id))
        if task.owner_plugin_id != owner:
            if task.owner_plugin_id:
                raise PluginServiceError("task_forbidden", "任务不属于该插件")
            raise PluginServiceError(
                "task_forbidden", "指令创建的任务只能通过 /exa 指令管理"
            )
        return task

    @_submission
    async def agent_get(self, plugin_id: str, task_id: str) -> dict[str, Any]:
        """查询归属任务；先校验本地归属，再刷新远端状态。

        返回 {"task": 快照, "remote_error": 远端刷新失败信息}。
        """
        owner = _owner_id(plugin_id)
        service = self._require_agent()
        task = await self._owned_task(service, owner, task_id)
        result = await service.get_task(task.task_id, exact=True)
        return {"task": task_snapshot(result.task), "remote_error": result.remote_error}

    @_submission
    async def agent_list(
        self,
        plugin_id: str,
        term: str = "",
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """按归属过滤后列出任务快照；SQL 先按 owner 过滤再 LIMIT。"""
        owner = _owner_id(plugin_id)
        if not isinstance(term, str):
            raise PluginServiceError("invalid_request", "term 必须是字符串")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PluginServiceError("invalid_request", "limit 必须是 1-100 的整数")
        service = self._require_agent()
        tasks = await service.search_tasks_for_owner(owner, term.strip(), limit)
        return [task_snapshot(task) for task in tasks]

    @_submission
    async def agent_cancel(self, plugin_id: str, task_id: str) -> dict[str, Any]:
        """取消归属任务；归属校验先于任何远端取消请求，返回任务快照。"""
        owner = _owner_id(plugin_id)
        service = self._require_agent()
        task = await self._owned_task(service, owner, task_id)
        cancelled = await service.cancel_task(task.task_id, exact=True)
        return task_snapshot(cancelled)
