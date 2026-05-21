---
name: lead-generation
description: 使用 Exa 深度搜索生成潜在客户列表。匹配 ICP 查找公司，用信号/新闻/评分进行丰富。适用于线索生成、构建潜在客户列表、outbound 调研或基于 ICP 的公司发现。
---

# 线索生成 (Lead Generation)

通过多轮 Exa 深度搜索，跨微垂直领域生成大规模、丰富的潜在客户列表。

## 可用工具

- `web_search_exa` — 语义搜索（使用 `search_type="deep"` 进行深度搜索）
- `exa_extract_web_page` — 提取公司网页详情做深度丰富
- `exa_find_similar` — 从已知目标公司发现相似公司

## 工作流程

```
第 1 步：ICP 调研（1 次 exa 搜索，用户确认/调整）
第 2 步：生成微垂直领域查询（LLM 推理，无 API 调用）
第 3 步：批量执行搜索
第 4 步：合并去重，按 ICP 匹配度排序
第 5 步：输出汇总
```

## 第 1 步：调研目标公司

用户说 "为 [公司] 生成 XX 条线索" 时，先搜索了解公司产品和 ICP：

```
web_search_exa(query="About {company_name}, {company_name} customers", search_type="deep", max_results=10)
```

向用户确认：
- ICP 描述是否准确？
- 有无需要增减的子领域？
- 有无需要排除的公司（竞品/已有客户）？
- 需要多少条线索？（默认 200）

## 第 2 步：查询扩展

将 ICP 拆分为多个**微垂直领域**查询，每个查询覆盖不同的嵌入空间：

### 扩展模式
1. **竞品挖掘** — "companies similar to {existing_customer}"
2. **地域划分** — 按地区细分
3. **公司阶段** — 种子期/成长期/企业级
4. **技术栈** — "companies using {relevant_tech}"
5. **用例分解** — 拆分为具体用例
6. **买家画像** — "companies with VP of Data Science"

### 查询质量标准
- 4-8 个描述性关键词
- 与其他查询不重叠
- 足够具体以返回相关公司
- 足够宽泛以返回 20+ 结果

## 第 3 步：批量搜索

对每个微垂直领域执行深度搜索：

```
web_search_exa(query="{micro_vertical}", search_type="deep", category="company", max_results=50)
```

对高价值目标，可用 `exa_find_similar` 扩展：
```
exa_find_similar(url="https://high-value-company.com", max_results=20)
```

## 第 4 步：去重排序

- 按公司名去重（忽略大小写，去除 Inc/Ltd 等后缀）
- 按 ICP 匹配度排序
- 标记关键信号（近期融资、招聘动态等）

## 第 5 步：汇总输出

报告：总线索数、去重数、匹配度分布。

## 性能提示

- 使用 `search_type="deep"` 获取最佳结果
- 每次搜索约返回 10-50 条结果
- 生成足够多的微垂直查询，按 `ceil(目标数 / 30)` 估算
- 大型列表（500+）执行前先确认用户意愿
