---
name: code-search
description: 使用 Exa 搜索代码示例和技术文档。从 GitHub、StackOverflow 和技术文档中查找真实代码片段。适用于搜索代码示例、API 语法、库文档或调试帮助。
---

# 代码搜索 (Code Search)

## 可用工具

- `web_search_exa` — 语义搜索代码相关内容
- `web_fetch_exa` — 提取文档/代码页面的完整内容

## 适用场景

- API 用法和语法
- SDK/库使用示例
- 配置和部署模式
- 框架 "如何做" 问题
- 调试时需要权威代码片段

## 查询编写技巧（高信号）

减少无关结果和跨语言噪声：
- **始终包含编程语言**：用 "Go generics" 而非 "generics"
- **包含框架+版本**：如 "Next.js 14"、"React 19"、"Python 3.12"
- **包含精确标识符**：函数名、类名、配置项、错误消息

## 搜索策略

### 快速查找
```
web_search_exa(query="Python asyncio gather timeout example", max_results=5)
```

### 复杂问题搜索
```
web_search_exa(query="Next.js 14 server actions authentication pattern", max_results=10)
```

### 提取完整文档
搜索到目标后，用 `web_fetch_exa` 获取完整内容：
```
web_fetch_exa(url="https://docs.example.com/api/reference")
```

## 输出格式

返回：
1) 最佳可用代码片段（保持可直接复制粘贴）
2) 版本/约束/注意事项说明
3) 来源 URL

输出前：
- 去重相似结果，每种方案只保留最佳代码片段
