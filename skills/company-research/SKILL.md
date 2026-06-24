---
name: company-research
description: 使用 Exa 搜索进行企业调研。查找公司信息、竞品、新闻、融资、LinkedIn 档案，构建公司清单。适用于企业研究、竞品分析、市场调研或构建公司列表。
---

# 企业调研 (Company Research)

## 可用工具

- `web_search_exa` — 语义搜索（支持 category 垂直分类）
- `web_fetch_exa` — 提取网页完整内容（深入了解某公司详情）
- `exa_find_similar` — 发现相似公司网站

## 搜索策略

### 分类选择

根据需求选择合适的 `category` 参数：
- `company` → 公司主页，富元数据（人数、地点、融资、营收）
- `news` → 新闻报道、公告
- `people` → LinkedIn 档案（公开数据）
- 不设 category → 通用搜索，更广泛的上下文

**建议流程：** 先用 `category: "company"` 发现公司，再用其他分类或不设分类做深入研究。

### 查询变体

Exa 对不同措辞返回不同结果。为提高覆盖率：
- 针对同一主题生成 2-3 个查询变体
- 合并去重结果

### 结果数量

根据用户意图动态调整 `max_results`：
- "找几个" → 10-20
- "全面调研" → 50-100
- 用户指定数量 → 匹配
- 不明确 → 询问用户

## 调研示例

### 发现某领域的公司
```
web_search_exa(query="AI infrastructure startups San Francisco", category="company", max_results=20)
```

### 深入研究某公司
```
web_search_exa(query="Anthropic funding rounds valuation 2024", max_results=10)
```
然后用 `web_fetch_exa` 提取关键页面的详细内容。

### 查找新闻报道
```
web_search_exa(query="Anthropic AI safety", category="news", max_results=15)
```

### 发现竞品
```
exa_find_similar(url="https://example.com", max_results=20)
```

## 输出格式

返回：
1) 结果列表（每行一家公司）
2) 来源（URL + 一句话说明相关性）
3) 备注（不确定性/信息冲突）
