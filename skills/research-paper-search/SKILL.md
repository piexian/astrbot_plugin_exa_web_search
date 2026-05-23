---
name: research-paper-search
description: 使用 Exa 搜索学术论文和科研内容。查找 arXiv 预印本、学术论文和科研成果。适用于搜索学术文献、论文综述或特定方法论的研究。
---

# 学术论文搜索 (Research Paper Search)

## 可用工具

- `web_search_exa` — 语义搜索学术论文（使用 `category="research paper"`）
- `exa_extract_web_page` — 提取论文/文档的完整内容
- `exa_find_similar` — 从已知论文发现相关研究

## 适用场景

- arXiv、OpenReview、PubMed 等平台的学术论文
- 特定主题的科研成果
- 带时间筛选的文献综述
- 包含特定方法论或术语的论文

## 搜索示例

### 某主题的近期论文
```
web_search_exa(query="transformer attention mechanisms efficiency 2024", category="research paper", max_results=15)
```

### 深度搜索特定领域
```
web_search_exa(query="large language model agents tool use", category="research paper", search_type="deep", max_results=20)
```

### 提取论文详细内容
```
exa_extract_web_page(url="https://arxiv.org/abs/2401.xxxxx")
```

### 发现相关论文
```
exa_find_similar(url="https://arxiv.org/abs/2401.xxxxx", max_results=15)
```

## 文献综述流程

1. 用 `web_search_exa` 搜索核心主题，获取初始论文列表
2. 用 `exa_find_similar` 从高质量论文扩展发现相关研究
3. 用 `exa_extract_web_page` 提取关键论文的摘要和方法论

## 输出格式

返回：
1) 结果（标题、作者、日期、摘要概述）
2) 来源（URL + 发表平台）
3) 备注（方法论差异、互相矛盾的发现）
