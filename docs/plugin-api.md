# 插件间调用（SDK v1）

SDK 与指令、LLM Tool 共用配置、Key 轮询、并发限制、预算和任务归档。使用 AstrBot 原生发现，不按安装目录导入本插件的类或异常。

## 发现与状态

```python
def get_exa_service(context):
    meta = context.get_registered_star("astrbot_plugin_exa_web_search")
    if meta is None:
        raise RuntimeError("注册表中未发现 Exa，请检查安装与加载状态")
    if not meta.activated:
        raise RuntimeError("Exa 插件已禁用")
    if meta.star_cls is None:
        raise RuntimeError("Exa 插件尚无可调用实例")
    getter = getattr(meta.star_cls, "get_service", None)
    if not callable(getter):
        raise RuntimeError("Exa 插件版本不支持 SDK，请升级")
    return getter(api_version=1)

service = get_exa_service(context)
status = service.get_status()
await service.wait_ready(timeout=5)
```

不要在消费者的 `initialize()` 中等待依赖；在事件处理或后台业务中使用有界等待。注册表缺失不等于磁盘未安装。

- `get_service` 同步返回本次加载的同一个门面，仅接受真正的整数 `1`，拒绝 bool。
- `get_status()` 返回 `api_version/instance_id/state/ready/reason/agent_ready`；状态与能力查询不联网、不初始化客户端、不读取任务库、不输出配置或凭据。
- `state` 为 `initializing/ready/unavailable/closing/closed`；`ready` 当且仅当状态为 `ready`。缺少 Key 时为 `unavailable/not_configured`。
- `agent_ready` 单独反映任务子系统状态；Agent 初始化失败不影响普通搜索。就绪仅代表本地条件，不保证网络、权限或余额。
- `capabilities()` 返回 `api_version=1` 与固定 `features=["web.search", "web.fetch", "code.search", "research.agent"]`。
- `wait_ready` 成功返回状态快照；超时抛 `TimeoutError`，关闭时抛带 `code="service_closed"` 的 `RuntimeError` 子类。重载后旧门面永久失效，需重新发现。

## 搜索与内容

```python
results = await service.search("AstrBot 插件开发", num_results=5)
pages = await service.fetch("https://example.org", max_characters=3000)
context_data = await service.code_context("how to parse yaml", tokens_num="dynamic")
```

| 方法 | 可选参数 | 返回 |
|---|---|---|
| `search(query, ...)` | `num_results/search_type/category/include_domains/exclude_domains/start_published_date/end_published_date/user_location/moderation/timeout` | 结果列表 |
| `fetch(url, ...)` | `max_characters/max_age_hours/timeout` | 内容列表 |
| `code_context(query, ...)` | `tokens_num/timeout` | 业务字典 |

参数归一化沿用现有业务入口；SDK 不增加指令专用重试，费用由配置的 Exa 账号承担。

## Exa Agent

```python
outcome = await service.agent_create("my_plugin", "调研 Exa API 的最新能力")
task_id = outcome["task"]["task_id"]
found = await service.agent_get("my_plugin", task_id)
tasks = await service.agent_list("my_plugin", limit=20)
# 确需取消时：await service.agent_cancel("my_plugin", task_id)
```

| 方法 | 返回 |
|---|---|
| `agent_create(plugin_id, query, *, event=None, notify=False)` | `{"task": snapshot, "warning": str}` |
| `agent_get(plugin_id, task_id)` | `{"task": snapshot, "remote_error": str}` |
| `agent_list(plugin_id, term="", *, limit=20)` | 快照列表，limit 为 1–100 的整数 |
| `agent_cancel(plugin_id, task_id)` | 取消后的任务快照 |

- `plugin_id` 必填、非空、最长 128 字符；仅用于协作归属，不是同进程恶意插件的鉴权边界。`query` 必须是非空字符串。
- 归属在本地任务预约时写入，早于远端创建；查询、取消只接受完整本地任务号，先检查归属再访问远端。其他插件与旧指令任务均拒绝访问；指令仍可管理全部任务。
- 已校验的任务若被并发清理，查询或取消会报任务不存在，不回退到另一个相同前缀的任务。
- 列表在 SQL 限额前按归属过滤。快照仅含 `task_id/run_id/status/query/result/sources/cost/error/created_at/updated_at/completed_at/owner_plugin_id`，不含 Key 槽、指纹、通知会话或内部路径。
- 默认不发通知。`notify=True` 必须传入有效的 `AstrMessageEvent` 和非空会话标识；参数错误在收费创建前拒绝。通知复用现有 UMO、场景与 message_id。
- 创建结果未知时仍返回本地任务号及 warning，由原任务服务继续核对；不会自动重建。调用方取消或超时后先按归属查询任务，不直接再次创建。
- 所有归属与指令共享并发和预算。未完成任务可在插件重新加载后沿用原恢复流程继续核对。

## 错误与兼容

基础错误为带 `.code` 的 `RuntimeError` 子类：`unsupported_version`、`not_ready`、`not_configured`、`service_closed`、`feature_disabled`、`invalid_request`、`task_forbidden`。其中 `feature_disabled` 表示 Agent 子系统未初始化成功；原有 API、参数、并发上限及任务不存在等业务异常保持原语义。

卸载先拒绝新请求，再等待已受理请求按底层超时结束并清理资源；关闭中断后旧门面也不会恢复。消费者不得关闭共享服务或资源。

首次加载新代码会为原 SQLite 表添加 `owner_plugin_id TEXT NOT NULL DEFAULT ''`，保留旧记录及历史字段。旧任务归属为空；回退旧代码可保留新增列，无需还原整个任务库。
