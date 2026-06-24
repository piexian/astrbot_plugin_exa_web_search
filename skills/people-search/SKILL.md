---
name: people-search
description: 使用 Exa 搜索查找人物信息。查找 LinkedIn 档案、专业背景、专家、团队成员和公开简介。适用于人物搜索、寻找专家或查看专业档案。
---

# 人物搜索 (People Search)

## 可用工具

- `web_search_exa` — 语义搜索人物信息（使用 `category="people"`）
- `web_fetch_exa` — 提取档案/简介页面的完整内容
- `exa_find_similar` — 从已知人物页面发现相似专家

## 分类选择

根据需求选择 `category` 参数：
- `people` → LinkedIn 档案、公开简介（发现阶段首选）
- `personal site` → 个人博客、作品集、关于页面
- `news` → 新闻报道、采访、演讲者简介
- 不设 category → 通用搜索，更广泛的上下文

**建议流程：** 先用 `category="people"` 发现人物，再用其他分类做深入调研。

## 结果数量

根据用户意图动态调整 `max_results`：
- "找几个" → 10-20
- "全面搜索" → 50-100
- 用户指定数量 → 匹配
- 不明确 → 询问用户

## 查询变体

Exa 对不同措辞返回不同结果。为提高覆盖率：
- 生成 2-3 个查询变体
- 合并去重

## 搜索示例

### 按角色发现人物
```
web_search_exa(query="VP Engineering AI infrastructure", category="people", max_results=20)
```

### 深入研究特定人物
```
web_search_exa(query="Dario Amodei Anthropic CEO background", max_results=15)
```
然后用 `web_fetch_exa` 提取详细页面内容。

### 新闻报道
```
web_search_exa(query="Dario Amodei interview 2024", category="news", max_results=10)
```

### 发现相似专家
```
exa_find_similar(url="https://linkedin.com/in/example-expert", max_results=20)
```

## 输出格式

返回：
1) 结果（姓名、职位、公司、地点）
2) 来源（档案 URL）
3) 备注（档案完整度、信息核实状态）
