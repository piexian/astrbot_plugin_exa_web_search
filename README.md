# Exa 联网搜索 - AstrBot 插件

通过 [Exa](https://exa.ai) API 为 AstrBot 提供联网搜索能力，支持指令和 LLM Tool 自动调用。

## 功能特性

- **语义搜索** — 基于 Exa 的搜索引擎，支持 7 种搜索类型（auto/neural/fast/deep-lite/deep/deep-reasoning/instant）
- **网页内容提取** — 提取指定 URL 的完整文本内容
- **相似页面发现** — 查找与给定 URL 语义相似的网页
- **多 API Key 轮询** — 支持配置多个 Key 进行轮询
- **指令 + LLM Tool** — 既可 `/exa` 手动搜索，也可由 AI 自动调用
- **自定义 Base URL** — 支持代理地址

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
| 默认搜索类型 | auto/neural/fast/deep-lite/deep/deep-reasoning/instant | `auto` |
| 显示来源 URL | 指令结果中是否显示来源 | `true` |
| 最大来源数量 | 显示的来源链接数量 | `5` |
| 最大重试次数 | 指令调用时的重试次数 | `3` |
| HTTP 代理 | 代理地址 | 空 |

## 使用方法

### 指令搜索

```
/exa help                         # 显示帮助
/exa Python 3.12 有什么新特性      # 执行搜索
```

### LLM Tool 自动调用

插件注册了 3 个 LLM Tool，大模型会在需要时自动调用：

- **`web_search_exa`** — 语义搜索（支持搜索类型和垂直分类）
- **`exa_extract_web_page`** — 提取网页完整内容
- **`exa_find_similar`** — 查找相似页面

例如，当你对 AI 说"帮我搜一下最近的 AI 新闻"时，模型会自动调用 `web_search_exa` 并整理结果回复你。

## 搜索类型说明

| 类型 | 说明 |
|------|------|
| `auto` | 智能选择最佳搜索方式（默认） |
| `neural` | 基于 embedding 的语义搜索 |
| `fast` | 快速搜索模式 |
| `instant` | 最低延迟搜索，适合实时应用 |
| `deep-lite` | 轻量级深度搜索 |
| `deep` | 标准深度搜索 |
| `deep-reasoning` | 深度推理搜索 |

## 垂直搜索分类

LLM Tool `web_search_exa` 支持以下分类：

`company` / `people` / `research paper` / `news` / `personal site` / `financial report`

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
├── .github/workflows/ci.yml   # CI：ruff lint/format + 语法检查 + 元数据校验
├── skills/                     # LLM 搜索技能指引（自动加载）
│   ├── company-research/SKILL.md       # 企业调研
│   ├── lead-generation/SKILL.md        # 线索生成
│   ├── code-search/SKILL.md            # 代码搜索
│   ├── people-search/SKILL.md          # 人物搜索
│   ├── financial-report-search/SKILL.md # 财务报告搜索
│   ├── research-paper-search/SKILL.md  # 学术论文搜索
│   └── personal-site-search/SKILL.md   # 个人站点搜索
├── tools/                      # Class-based LLM 工具定义
│   ├── __init__.py
│   └── exa_tools.py            # web_search_exa, exa_extract_web_page, exa_find_similar
├── _conf_schema.json           # AstrBot 控制台配置 UI 定义
├── main.py                     # 插件核心逻辑 (指令注册和初始化)
├── metadata.yaml               # 插件元信息
├── README.md                   # 说明文档
└── LICENSE                     # GPL-3.0 许可证
```

## 相关链接

- [Exa 官方文档](https://docs.exa.ai)
- [Exa Dashboard](https://dashboard.exa.ai)
- [AstrBot 官方文档](https://docs.astrbot.app)

## 许可证

GPL-3.0 License
