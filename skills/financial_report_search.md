---
name: financial-report-search
description: 使用 Exa 搜索财务报告。查找 SEC 文件、财报、年报和财务文件。适用于搜索 10-K 文件、季度财报或年度报告。
---

# 财务报告搜索 (Financial Report Search)

## 可用工具

- `web_search_exa` — 语义搜索财务报告（使用 `category="financial report"`）
- `exa_extract_web_page` — 提取报告/文件的完整内容

## 适用场景

- SEC 文件（10-K、10-Q、8-K、S-1）
- 季度财报
- 年度报告
- 投资者演示文稿
- 财务报表

## 搜索示例

### 查找公司 SEC 文件
```
web_search_exa(query="Anthropic SEC filing S-1", category="financial report", max_results=10)
```

### 查找近期财报
```
web_search_exa(query="Q4 2025 earnings report technology", category="financial report", max_results=20)
```

### 特定类型文件
```
web_search_exa(query="10-K annual report AI companies", category="financial report", search_type="deep", max_results=15)
```

### 深入阅读报告
搜索到目标后，用 `exa_extract_web_page` 获取完整内容：
```
exa_extract_web_page(url="https://sec.gov/example-filing")
```

## 输出格式

返回：
1) 结果（公司名、文件类型、日期、关键数据/亮点）
2) 来源（文件 URL）
3) 备注（报告期间、修正、审计意见）
