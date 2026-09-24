# 更新日志

## v1.1.0 - 2026-09-24

- 移除已废弃的 `find-similar` 工具。
- 对齐 Exa 搜索契约：搜索类型更新为 `instant`/`fast`/`auto`/`deep-lite`/`deep`/`deep-reasoning`，工具更名 `exa-search`，按官方规则校验垂直分类过滤参数。
- 新增 `exa-code-context` LLM Tool，获取代码示例和实现上下文。
- 新增搜索内容控制：Exa 内容审核开关、`user_location`、`max_age_hours` 内容新鲜度、Highlights 默认返回。
- 新增 Exa Agent 异步研究任务与 `/exa` 管理命令（`-r` 创建、`-s` 查询、`stats`/`clean`/`cancel`），任务归档持久化到 SQLite，支持结果文件发送与容量告警。
- 修复无效 Base URL 回退和 429 限流重试，统一 AGPL-3.0 许可声明。

## v1.0.3 - 2026-06-24

- 将 Exa 搜索类型收敛为 `auto`、`keyword`、`neural`，默认继续使用 `auto`。
- 将内容提取 LLM Tool 从 `exa_extract_web_page` 更名为 `web_fetch_exa`。
- 更新 README 和内置 Skills，避免继续引导模型使用旧搜索类型。

## v1.0.1 - 2026-05-23

- 修复插件内置 Skills 不能被 AstrBot 自动加载的问题。
- 将 Skills 调整为 AstrBot 识别的 `skills/<skill-name>/SKILL.md` 目录结构。
- 更新 README 中的项目结构说明。
