# 更新日志

## v1.0.3 - 2026-06-24

- 将 Exa 搜索类型收敛为 `auto`、`keyword`、`neural`，默认继续使用 `auto`。
- 将内容提取 LLM Tool 从 `exa_extract_web_page` 更名为 `web_fetch_exa`。
- 更新 README 和内置 Skills，避免继续引导模型使用旧搜索类型。

## v1.0.1 - 2026-05-23

- 修复插件内置 Skills 不能被 AstrBot 自动加载的问题。
- 将 Skills 调整为 AstrBot 识别的 `skills/<skill-name>/SKILL.md` 目录结构。
- 更新 README 中的项目结构说明。
