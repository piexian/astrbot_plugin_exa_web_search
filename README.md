# Exa 联网搜索 - AstrBot 插件

通过 [Exa](https://exa.ai) API 为 AstrBot 提供联网搜索能力，支持指令和 LLM Tool 自动调用。

## 功能特性

- **语义搜索** — 基于 Exa 的搜索引擎，默认使用推荐的 `auto` 搜索类型
- **代码上下文** — 使用 Exa Code 获取真实代码示例和实现上下文
- **网页内容提取** — 提取指定 URL 的完整文本内容
- **多 API Key 轮询** — 支持配置多个 Key 进行轮询
- **指令 + LLM Tool** — 既可 `/exa` 手动搜索，也可由 AI 自动调用
- **自定义 Base URL** — 支持代理地址
- **Agent 异步研究** — 管理员可创建、查询、恢复和归档 Exa Agent 任务

## 安装

在 AstrBot 控制台的插件市场中搜索 `Exa联网搜索` 安装，或通过 GitHub 仓库地址安装：

```
https://github.com/piexian/astrbot_plugin_exa_web_search
```

## 配置

### 获取 API Key

1. 前往 [Exa Dashboard](https://dashboard.exa.ai/api-keys) 注册并获取 API Key
2. 在 AstrBot 控制台 → 插件设置 → Exa联网搜索 → 填入 API Key

### 配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| Exa API Key 列表 | 支持多个 Key 进行轮询 | `[]` |
| Exa API Base URL | 自定义 API 地址（代理/中转站） | `https://api.exa.ai` |
| 请求超时时间 | API 请求最大等待秒数 | `30` |
| 搜索返回最大条数 | 搜索结果数量（1-100） | `10` |
| 默认搜索类型 | instant/fast/auto/deep-lite/deep/deep-reasoning（推荐 auto） | `auto` |
| 启用内容审核 | 由 Exa 过滤不安全内容 | `false` |
| 显示来源 URL | 指令结果中是否显示来源 | `true` |
| 最大来源数量 | 显示的来源链接数量 | `5` |
| 最大重试次数 | 指令调用时的重试次数 | `3` |
| Agent 最大并发任务数 | 同时运行的 Agent 任务上限 | `2` |
| Agent 状态轮询间隔 | 状态查询间隔（秒） | `5` |
| Agent 研究强度 | minimal/low/medium/high/xhigh/auto/max | `auto` |
| Agent 单任务预算 | 单个任务预算上限（美元） | `5` |
| 任务归档容量上限 | SQLite 主库、WAL、SHM 总容量（MB） | `100` |
| HTTP 代理 | 代理地址 | 空 |

无效或空白的 Base URL 会使插件加载失败。

## 使用方法

### 指令搜索

```
/exa help                         # 显示帮助
/exa Python 3.12 有什么新特性      # 执行搜索
```

### Agent 任务（管理员）

```
/exa -r <研究问题>       # 创建异步研究任务
/exa -s [筛选词]         # 按任务号、日期、状态或关键词查询
/exa stats <任务号>      # 查看状态、结果、费用和容量
/exa stats -q <任务号>   # 发送 Markdown 归档文件
/exa clean <任务号|all>  # 清理终态任务（all 需确认）
/exa cancel <任务号>     # 取消活动任务
```

任务结束后会自动返回一次“任务号 + 正文”；完整消息超过 1500 字符时改发以任务号命名、仅含任务号和正文的 Markdown 文件。普通 `stats` 不发送文件，只有显式使用 `stats -q` 才导出归档。

文件发送失败时降级为分段全文并保存发送进度，送达后释放快照；网络超时、断连仍走通知重试。临时文件在本次发送结束后清理。

任务归档保存在插件数据目录的 `exa_tasks.sqlite3`，重启后仍可查询。归档容量达到 80% 时告警，超过 100% 自动删除最旧的终态任务；活动任务不会被自动删除。

### LLM Tool 自动调用

插件注册了 3 个 LLM Tool，大模型会在需要时自动调用：

- **`exa-code-context`** — 获取代码示例和实现上下文
- **`exa-search`** — 语义搜索（支持搜索类型和垂直分类）
- **`web_fetch_exa`** — 提取网页完整内容
  - `exa-search` 默认返回 Highlights；`web_fetch_exa` 支持 `max_age_hours` 控制内容新鲜度，`exa-search` 支持 `user_location`。

例如，当你对 AI 说"帮我搜一下最近的 AI 新闻"时，模型会自动调用 `exa-search` 并整理结果回复你。

## 搜索类型说明

| 类型 | 说明 |
|------|------|
| `instant` | 最低延迟，适合实时场景 |
| `fast` | 低延迟高质量搜索 |
| `auto` | 智能平衡速度和质量（默认，推荐） |
| `deep-lite` | 轻量深度研究 |
| `deep` | 多步研究和综合 |
| `deep-reasoning` | 复杂推理任务 |

旧配置 `keyword`、`neural` 和旧分类 `research paper` 会分别按 `auto`、`publication` 处理。`deep` 和 `deep-reasoning` 的请求超时至少为 60 秒和 90 秒。

## 垂直搜索分类

LLM Tool `exa-search` 支持以下分类：

`company` / `people` / `publication` / `news` / `personal site` / `financial report`

`people` 不支持 `excludeDomains`、`startPublishedDate` 或 `endPublishedDate`；`company` 不支持后两个日期过滤参数。

## EXA额度

免费给的10刀额度不够用怎么办，前往[账单界面](https://dashboard.exa.ai/billing)右下角输入兑换
<img width="3430" height="1893" alt="image" src="https://github.com/user-attachments/assets/f4cd7d46-c0e8-4506-b19f-0c17de895f0c" />

50赠金
```
EXA50BUILDCLUB
```

## 项目结构

```
astrbot_plugin_exa_web_search/
├── .github/workflows/ci.yml   # CI：单元测试 + ruff lint/format + 语法检查 + 元数据校验
├── tests/                     # 搜索、内容和代码上下文单元测试
├── skills/                     # LLM 搜索技能指引（自动加载）
│   ├── company-research/SKILL.md       # 企业调研
│   ├── lead-generation/SKILL.md        # 线索生成
│   ├── code-search/SKILL.md            # 代码搜索
│   ├── people-search/SKILL.md          # 人物搜索
│   ├── financial-report-search/SKILL.md # 财务报告搜索
│   ├── research-paper-search/SKILL.md  # 学术论文搜索
│   └── personal-site-search/SKILL.md   # 个人站点搜索
├── tools/                      # 搜索、Agent API 与归档模块
│   ├── __init__.py
│   ├── exa_agent.py            # Agent API 客户端与响应规范化
│   ├── exa_commands.py         # 指令前缀解析
│   ├── exa_context.py          # Exa Code Context 参数校验
│   ├── exa_content.py          # Contents 内容参数校验
│   ├── exa_files.py             # 归档 Markdown 与容量展示
│   ├── exa_search.py           # 搜索参数规范化与校验
│   ├── exa_task_service.py     # Agent 生命周期与恢复
│   ├── exa_tasks.py             # SQLite 任务归档
│   └── exa_tools.py             # LLM Tool 定义
├── _conf_schema.json           # AstrBot 控制台配置 UI 定义
├── main.py                     # 插件核心逻辑 (指令注册和初始化)
├── metadata.yaml               # 插件元信息
├── README.md                   # 说明文档
└── LICENSE                     # AGPL-3.0 许可证
```

## 相关链接

- [Exa 官方文档](https://docs.exa.ai)
- [Exa Dashboard](https://dashboard.exa.ai)
- [AstrBot 官方文档](https://docs.astrbot.app)

## 许可证

AGPL-3.0 License
