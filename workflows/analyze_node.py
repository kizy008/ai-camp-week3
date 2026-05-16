"""
分析节点 — 用 LLM 对每条数据生成中文摘要、标签、评分

职责:
  遍历 state.sources，逐条调用 chat_json() 让 LLM 生成结构化分析结果:
    - summary: 50-100 字中文摘要
    - tags: 3 个标签
    - rating: 0.0-1.0 质量评分
    - category: 理论/工具/应用/教程

异常处理:
  单条 LLM 调用失败 → 插入降级结果（rating=0），不影响后续条目的处理
"""

from workflows.model_client import accumulate_usage, chat_json
from workflows.state import KBState

# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = "你是一个专业的 AI 技术分析师。请用 JSON 格式回复，只输出 JSON 对象，不要多余内容。"
"""system prompt：强制 JSON 输出，减少 token 浪费"""

_USER_PROMPT = """分析以下 GitHub 仓库信息，生成中文分析结果：

名称: {name}
描述: {description}
URL: {url}
编程语言: {language}
星标数: {stars}
主题标签: {topics}

请返回以下 JSON 结构（严格遵循）:
{{
  "summary": "中文摘要（50-100 字，概括仓库功能与价值）",
  "tags": ["标签1", "标签2", "标签3"],
  "rating": 0.0-1.0,
  "category": "理论 | 工具 | 应用 | 教程"
}}"""
"""user prompt：分析单条仓库，format 后调用"""


# ---------------------------------------------------------------------------
# 节点函数
# ---------------------------------------------------------------------------


def analyze_node(state: KBState) -> dict:
    """遍历 sources，用 LLM 为每条数据生成结构化分析。

    Returns:
        {"analyses": list[dict], "cost_tracker": dict}
        — 每个分析 dict 包含 url, name, summary, tags, rating, category
    """
    print("[AnalyzeNode] 开始 LLM 分析")

    sources = state.get("sources", [])
    if not sources:
        print("  [AnalyzeNode] 无数据需分析")
        return {"analyses": [], "cost_tracker": state.get("cost_tracker", {})}

    analyses: list[dict] = []
    tracker = dict(state.get("cost_tracker", {}))

    for i, src in enumerate(sources):
        name = src.get("name", "unknown")
        print(f"  [AnalyzeNode] 分析第 {i + 1}/{len(sources)} 条: {name}")

        prompt = _USER_PROMPT.format(
            name=name,
            description=src.get("description", ""),
            url=src.get("url", ""),
            language=src.get("language", ""),
            stars=src.get("stars", 0),
            topics=", ".join(src.get("topics", [])),
        )

        try:
            result, usage = chat_json(prompt, system=_SYSTEM_PROMPT)
            tracker = accumulate_usage(tracker, usage)
        except Exception as e:
            print(f"  [AnalyzeNode] LLM 分析失败: {e}")
            result = {"summary": "分析失败", "tags": [], "rating": 0.0, "category": "其他"}

        analyses.append(
            {
                "url": src.get("url", ""),
                "name": name,
                "summary": result.get("summary", ""),
                "tags": result.get("tags", []),
                "rating": float(result.get("rating", 0.0)),
                "category": result.get("category", "其他"),
            }
        )

    print(f"  [AnalyzeNode] 完成 {len(analyses)} 条分析")
    return {"analyses": analyses, "cost_tracker": tracker}
