import asyncio
import json
import time
from urllib.parse import urlparse

import aiohttp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.star.filter.command import GreedyStr

from .tools.exa_content import build_contents_payload
from .tools.exa_context import build_context_payload
from .tools.exa_response import normalize_cost_total
from .tools.exa_search import (
    MIN_TIMEOUT_SECONDS,
    build_search_payload,
    get_result_snippet,
    normalize_search_type,
    resolve_timeout_seconds,
)

PLUGIN_NAME = "astrbot_plugin_exa_web_search"

# 可重试 HTTP 状态码
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503})

# Base URL 禁止的端点路径后缀
_DISALLOWED_PATH_SUFFIXES = frozenset({"search", "contents", "answer", "context"})


# API Key 轮询器


class _KeyRotator:
    """并发安全的 Round-Robin API Key 轮询器。"""

    def __init__(self):
        self._lock = asyncio.Lock()
        self._index = 0

    async def get(self, keys: list[str]) -> str:
        """从 key 列表中获取下一个可用的 key。"""
        if not keys:
            raise ValueError("Exa API Key 列表为空，请在插件配置中添加 API Key。")
        async with self._lock:
            key = keys[self._index % len(keys)]
            self._index += 1
            return key


# Exa API 错误类


class ExaAPIError(Exception):
    """Exa API 请求错误，携带 HTTP 状态码、错误标签和 requestId。"""

    def __init__(
        self,
        message: str,
        status: int = 0,
        tag: str = "",
        request_id: str = "",
    ):
        super().__init__(message)
        self.status = status
        self.tag = tag
        self.request_id = request_id

    @property
    def retryable(self) -> bool:
        return self.status in _RETRYABLE_STATUS_CODES


# 工具函数


def _normalize_base_url(base_url: str) -> str:
    """规范化 Base URL：去尾斜杠、校验协议、拒绝端点路径后缀。"""
    normalized = (base_url or "").strip().rstrip("/")
    if not normalized:
        return "https://api.exa.ai"

    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(
            f"Exa API Base URL 必须以 http:// 或 https:// 开头，当前值: {normalized!r}"
        )

    last_segment = parsed.path.rstrip("/").rsplit("/", 1)[-1].lower()
    if last_segment and last_segment in _DISALLOWED_PATH_SUFFIXES:
        raise ValueError(
            f"Exa API Base URL 应为基础地址，不应包含具体端点路径 "
            f"（如 /search、/contents、/context），当前值: {normalized!r}"
        )
    return normalized


def _normalize_count(value, *, default: int, minimum: int, maximum: int) -> int:
    """安全解析整数参数并限制范围。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(n, maximum))


def _normalize_timeout(timeout_seconds) -> aiohttp.ClientTimeout:
    """构造 aiohttp 超时对象，确保最小值。"""
    timeout = resolve_timeout_seconds(timeout_seconds)
    return aiohttp.ClientTimeout(total=timeout)


def _format_exa_status_error(statuses: list[dict]) -> str | None:
    """检查响应中的 statuses 字段，返回错误描述或 None。"""
    errors = []
    for item in statuses:
        if item.get("status") == "success":
            continue
        err = item.get("error", {})
        tag = err.get("tag", "UNKNOWN")
        http_code = err.get("httpStatusCode", "")
        url_id = item.get("id", "unknown URL")
        code_info = f" (HTTP {http_code})" if http_code else ""
        errors.append(f"{url_id}: {tag}{code_info}")
    if errors:
        return "Exa 内容提取失败: " + "; ".join(errors)
    return None


def _mask_key(key: str) -> str:
    """脱敏 API Key，保留前 4 后 4 位，中间用 **** 替代。"""
    key = str(key).strip()
    if len(key) <= 8:
        return key[:2] + "****"
    return key[:4] + "****" + key[-4:]


# 插件主类
class ExaWebSearchPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self._session: aiohttp.ClientSession | None = None
        self._key_rotator = _KeyRotator()
        self._base_url: str = "https://api.exa.ai"

        # 注册 LLM 函数工具
        from .tools.exa_tools import (
            ExaCodeContextTool,
            ExaSearchTool,
            ExaWebFetchTool,
        )

        self.context.add_llm_tools(
            ExaSearchTool(plugin=self),
            ExaCodeContextTool(plugin=self),
            ExaWebFetchTool(plugin=self),
        )

    async def initialize(self):
        """插件初始化：验证配置并规范化 Base URL。"""
        # 解析并验证 Base URL
        raw_url = self.config.get("exa_base_url", "https://api.exa.ai")
        try:
            self._base_url = _normalize_base_url(raw_url)
        except ValueError as e:
            logger.error(f"[{PLUGIN_NAME}] {e}")
            return

        # 检查 API Key
        keys = self.config.get("exa_api_keys", [])
        if not keys:
            logger.warning(
                f"[{PLUGIN_NAME}] 未配置 Exa API Key，"
                f"请前往插件设置填写（获取: https://dashboard.exa.ai/api-keys）"
            )
        else:
            masked = ", ".join(_mask_key(k) for k in keys)
            logger.info(
                f"[{PLUGIN_NAME}] 已加载 {len(keys)} 个 API Key [{masked}]，"
                f"Base URL: {self._base_url}"
            )

    async def terminate(self):
        """插件销毁：关闭 HTTP 会话。"""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp 会话。"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(trust_env=True)
        return self._session

    def _get_proxy(self) -> str | None:
        """获取代理配置。"""
        return self.config.get("proxy", "").strip() or None

    async def _get_api_key(self) -> str:
        """获取下一个可用的 API Key。"""
        keys = self.config.get("exa_api_keys", [])
        return await self._key_rotator.get(keys)

    async def _exa_request(
        self,
        endpoint: str,
        payload: dict,
        timeout: int | None = None,
    ) -> dict:
        """统一的 Exa API 请求方法。

        Args:
            endpoint: API 端点路径（如 "/search"）
            payload: 请求体
            timeout: 超时秒数

        Returns:
            响应 JSON 字典

        Raises:
            ExaAPIError: 请求失败时抛出
        """
        if timeout is None:
            timeout = self.config.get("timeout_seconds", MIN_TIMEOUT_SECONDS)

        api_key = await self._get_api_key()
        url = f"{self._base_url}{endpoint}"
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }
        proxy = self._get_proxy()
        session = self._get_session()

        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=_normalize_timeout(timeout),
                proxy=proxy,
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # 记录费用信息
                    cost = data.get("costDollars", {})
                    total = normalize_cost_total(cost)
                    logger.debug(f"[{PLUGIN_NAME}] {endpoint} 费用: ${total}")
                    return data

                # 错误处理
                raw_text = await resp.text()
                err_data = {}
                try:
                    err_data = json.loads(raw_text)
                except (json.JSONDecodeError, ValueError):
                    pass
                error_msg = (
                    err_data.get("error", raw_text)
                    if isinstance(err_data, dict)
                    else raw_text
                )
                tag = err_data.get("tag", "") if isinstance(err_data, dict) else ""
                request_id = (
                    err_data.get("requestId", "") if isinstance(err_data, dict) else ""
                )

                if request_id:
                    logger.warning(
                        f"[{PLUGIN_NAME}] Exa 请求失败 "
                        f"[{endpoint}] HTTP {resp.status} "
                        f"tag={tag} requestId={request_id}: {error_msg}"
                    )

                if resp.status == 402:
                    friendly = f"Exa API 额度已耗尽。（{error_msg}）"
                    raise ExaAPIError(
                        friendly,
                        status=resp.status,
                        tag=tag,
                        request_id=request_id,
                    )

                raise ExaAPIError(
                    f"Exa API 请求失败 [{endpoint}]: "
                    f"HTTP {resp.status} [{tag}] {error_msg}",
                    status=resp.status,
                    tag=tag,
                    request_id=request_id,
                )

        except aiohttp.ClientError as e:
            raise ExaAPIError(
                f"Exa API 网络错误 [{endpoint}]: {e}",
                status=0,
            ) from e
        except asyncio.TimeoutError:
            raise ExaAPIError(
                f"Exa API 请求超时 [{endpoint}]（{timeout}s）",
                status=0,
            )

    async def _exa_search(
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
    ) -> list[dict]:
        """调用 Exa search 端点。"""
        if moderation is None:
            moderation = bool(self.config.get("moderation", False))
        payload = build_search_payload(
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
        )
        if timeout is None:
            timeout = self.config.get("timeout_seconds", MIN_TIMEOUT_SECONDS)
        request_timeout = resolve_timeout_seconds(timeout, payload["type"])
        data = await self._exa_request("/search", payload, timeout=request_timeout)
        return data.get("results", [])

    async def _exa_code_context(
        self,
        query: str,
        *,
        tokens_num: str | int = "dynamic",
        timeout: int | None = None,
    ) -> dict:
        """调用 Exa Code Context 端点获取代码上下文。"""
        payload = build_context_payload(query, tokens_num=tokens_num)
        if timeout is None:
            timeout = self.config.get("timeout_seconds", MIN_TIMEOUT_SECONDS)
        return await self._exa_request("/context", payload, timeout=timeout)

    async def _exa_extract(
        self,
        url: str,
        *,
        max_characters: int = 3000,
        max_age_hours: int | None = None,
        timeout: int | None = None,
    ) -> list[dict]:
        """调用 Exa contents 端点提取网页内容。

        与 Exa 官方保持一致：通过 ids 提取内容。
        """
        payload = build_contents_payload(
            url,
            max_characters=max_characters,
            max_age_hours=max_age_hours,
        )

        data = await self._exa_request("/contents", payload, timeout=timeout)

        # 检查逐 URL 状态
        statuses = data.get("statuses", [])
        if statuses:
            status_error = _format_exa_status_error(statuses)
            if status_error:
                raise ValueError(status_error)

        return data.get("results", [])

    async def _search_with_retry(
        self,
        query: str,
        *,
        num_results: int = 10,
        search_type: str = "auto",
        category: str = "",
    ) -> dict:
        """带重试的搜索方法，仅供 /exa 指令使用。

        Returns:
            dict: {"ok": bool, "results": list,
                "elapsed_ms": float, "retries": int,
                "error": str}
        """
        max_retries = self.config.get("max_retries", 3)
        retry_delay = self.config.get("retry_delay", 1.0)

        start = time.monotonic()
        last_error = ""
        retries = 0

        for attempt in range(max_retries + 1):
            try:
                results = await self._exa_search(
                    query,
                    num_results=num_results,
                    search_type=search_type,
                    category=category,
                )
                elapsed = (time.monotonic() - start) * 1000
                return {
                    "ok": True,
                    "results": results,
                    "elapsed_ms": elapsed,
                    "retries": retries,
                }

            except ExaAPIError as e:
                last_error = str(e)
                if not e.retryable or attempt >= max_retries:
                    break
                retries += 1
                # 指数退避
                delay = retry_delay * (2**attempt)
                logger.info(
                    f"[{PLUGIN_NAME}] 搜索失败（HTTP {e.status}），"
                    f"{delay:.1f}s 后重试 ({retries}/{max_retries})"
                )
                await asyncio.sleep(delay)

            except Exception as e:
                last_error = str(e)
                break

        elapsed = (time.monotonic() - start) * 1000
        return {
            "ok": False,
            "results": [],
            "elapsed_ms": elapsed,
            "retries": retries,
            "error": last_error,
        }

    def _render_sources(
        self,
        results: list[dict],
        *,
        header: str,
        with_snippet: bool,
    ) -> list[str]:
        """渲染来源列表"""
        if not self.config.get("show_sources", True) or not results:
            return []
        max_sources = self.config.get("max_sources", 5)
        if max_sources > 0:
            results = results[:max_sources]
        lines = [f"\n{header}:"]
        for i, item in enumerate(results, 1):
            url = item.get("url", "")
            title = item.get("title", "")
            if title:
                if with_snippet:
                    lines.append(f"  {i}. {title}")
                    lines.append(f"     {url}")
                else:
                    lines.append(f"  {i}. {title}\n     {url}")
            else:
                lines.append(f"  {i}. {url}")
            if with_snippet:
                snippet = get_result_snippet(item)[:200]
                if snippet:
                    lines.append(f"     {snippet}")
        return lines

    def _format_result(self, result: dict) -> str:
        """格式化搜索结果为用户友好的消息（exa 指令输出）。"""
        if not result.get("ok"):
            return f"搜索失败: {result.get('error', '未知错误')}"

        results = result.get("results", [])
        elapsed = result.get("elapsed_ms", 0) / 1000

        if not results:
            return "未找到相关结果。"

        lines = []
        for item in results:
            title = item.get("title", "")
            text = get_result_snippet(item)[:300]
            if title:
                lines.append(f"**{title}**")
            if text:
                lines.append(text)
            lines.append("")

        # 来源列表
        lines.extend(self._render_sources(results, header="来源", with_snippet=False))

        # 耗时和重试
        retry_info = ""
        retries = result.get("retries", 0)
        if retries > 0:
            retry_info = f"，重试 {retries} 次"
        lines.append(f"\n(耗时: {elapsed:.1f}s{retry_info})")

        return "\n".join(lines)

    def _help_text(self) -> str:
        """返回帮助文本。"""
        keys = self.config.get("exa_api_keys", [])
        if keys:
            key_status = ", ".join(_mask_key(k) for k in keys)
        else:
            key_status = "未配置 "
        search_type = normalize_search_type(self.config.get("default_search_type"))
        max_results = self.config.get("max_results", 10)

        return (
            "Exa 联网搜索\n"
            "\n"
            "用法:\n"
            "  /exa help           显示此帮助\n"
            "  /exa <搜索内容>     执行联网搜索\n"
            "\n"
            "示例:\n"
            "  /exa Python 3.12 有什么新特性\n"
            "  /exa 最新的 AI 新闻\n"
            "  /exa SpaceX 最新估值\n"
            "\n"
            "调用方式:\n"
            "  - /exa 指令：直接搜索并返回结果\n"
            "  - LLM Tool：模型自动调用 exa-search\n"
            "  - LLM Tool：模型自动调用 exa-code-context\n"
            "  - LLM Tool：模型自动调用 web_fetch_exa\n"
            "\n"
            f"当前配置:\n"
            f"  API Key: {key_status}\n"
            f"  Base URL: {self._base_url}\n"
            f"  搜索类型: {search_type}\n"
            f"  最大结果数: {max_results}"
        )

    @filter.command("exa")
    async def exa_command(self, event: AstrMessageEvent, query: GreedyStr = ""):
        """执行搜索并返回结果。exa help 显示帮助文本。"""
        query = query.strip()

        if not query or query.lower() == "help":
            yield event.plain_result(self._help_text())
            return

        # 检查 API Key
        keys = self.config.get("exa_api_keys", [])
        if not keys:
            yield event.plain_result(
                "请先配置 Exa API Key（前往 https://dashboard.exa.ai/api-keys 获取）"
            )
            return

        # 获取配置
        search_type = normalize_search_type(self.config.get("default_search_type"))
        max_results = _normalize_count(
            self.config.get("max_results", 10),
            default=10,
            minimum=1,
            maximum=100,
        )

        # 带重试搜索
        result = await self._search_with_retry(
            query,
            num_results=max_results,
            search_type=search_type,
        )

        yield event.plain_result(self._format_result(result))
