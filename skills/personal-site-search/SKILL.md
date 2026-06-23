---
name: personal-site-search
description: 使用 Exa 搜索个人网站和博客。查找个人观点、作品集和独立博客。适用于搜索个人站点、技术博客或作品集网站。
---

# 个人站点搜索 (Personal Site Search)

## 可用工具

- `web_search_exa` — 语义搜索个人站点（使用 `category="personal site"`）
- `web_fetch_exa` — 提取博客/文章的完整内容
- `exa_find_similar` — 从已知博客发现相似作者

## 适用场景

- 个人专家的观点和经验分享
- 技术博客文章
- 作品集网站
- 独立分析（非企业内容）
- 从业者的深度教程和实践

## 搜索示例

### 技术博客文章
```
web_search_exa(query="building production LLM applications lessons learned", category="personal site", max_results=15)
```

### 某主题的近期文章
```
web_search_exa(query="Rust async runtime comparison 2025", category="personal site", max_results=10)
```

### 提取文章全文
```
web_fetch_exa(url="https://example-blog.com/llm-lessons")
```

### 发现相似博客
```
exa_find_similar(url="https://example-blog.com", max_results=15)
```

## 输出格式

返回：
1) 结果（标题、作者/站点名、日期、关键洞察）
2) 来源 URL
3) 备注（作者专业背景、潜在偏见、内容深度）
