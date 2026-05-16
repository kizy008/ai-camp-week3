"""
整理节点 — 过滤低分条目、按 URL 去重、可选 LLM 修正

职责:
  三阶段处理:
    1. 修正（仅在有审核反馈时触发）: iteration > 0 且有 feedback 时，
       调用 LLM 对每条分析做定向修改
    2. 过滤: 丢弃 rating < 0.6 的低质条目
    3. 去重: 以 url 为键，同 URL 保留评分高的

设计原则:
  - 修正阶段和过滤阶段通过 iteration + feedback 自然区分，不依赖路由标志
  - 修正失败不影响已有数据，保留原始分析结果
"""

import json

from workflows.model_client import accumulate_usage, chat_json
from workflows.state import KBState

# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

_SYSTEM_REVISE = "你是一个专业的 AI 知识库编辑。请根据审核反馈定向修改分析结果，只输出 JSON。"
"""修正模式的 system prompt"""

_USER_REVISE = """请根据以下审核反馈，修改当前的分析结果：

=== 审核反馈 ===
{feedback}

=== 当前分析 ===
{analysis_json}

请返回与原来相同结构的 JSON：
{{
  "summary": "修改后的中文摘要",
  "tags": ["标签1", "标签2", ...],
  "rating": 0.0-1.0,
  "category": "理论 | 工具 | 应用 | 教程"
}}

针对反馈中的每一条意见做出对应修改。"""
"""修正模式的 user prompt：将审核反馈与分析原文一同送入 LLM"""


# ---------------------------------------------------------------------------
# 节点函数
# ---------------------------------------------------------------------------


def _revise_analyses(
    analyses: list[dict], feedback: str, tracker: dict
) -> tuple[list[dict], dict]:
    """调用 LLM 逐条修正分析结果。

    Args:
        analyses: 当前分析列表
        feedback: 审核反馈文本
        tracker: 当前 token 追踪器

    Returns:
        (修正后的 analyses, 更新后的 tracker)
    """
    revised: list[dict] = []
    for i, item in enumerate(analyses):
        print(f"  [OrganizeNode] 修正第 {i + 1}/{len(analyses)} 条")
        prompt = _USER_REVISE.format(
            feedback=feedback,
            analysis_json=json.dumps(item, ensure_ascii=False),
        )
        try:
            result, usage = chat_json(prompt, system=_SYSTEM_REVISE)
            tracker = accumulate_usage(tracker, usage)
            revised.append(
                {
                    "url": item.get("url", ""),
                    "name": item.get("name", ""),
                    "summary": result.get("summary", item.get("summary", "")),
                    "tags": result.get("tags", item.get("tags", [])),
                    "rating": float(result.get("rating", item.get("rating", 0.0))),
                    "category": result.get("category", item.get("category", "其他")),
                }
            )
        except Exception as e:
            print(f"  [OrganizeNode] 修正失败，保留原文: {e}")
            revised.append(item)
    return revised, tracker


def _filter_by_rating(analyses: list[dict], threshold: float = 0.6) -> list[dict]:
    """过滤低评分条目。"""
    return [a for a in analyses if a.get("rating", 0) >= threshold]


def _deduplicate_by_url(analyses: list[dict]) -> list[dict]:
    """按 URL 去重，同 URL 保留评分高的条目。"""
    seen: dict[str, dict] = {}
    for item in analyses:
        url = item.get("url", "")
        if url in seen:
            if item.get("rating", 0) > seen[url].get("rating", 0):
                seen[url] = item
        else:
            seen[url] = item
    return list(seen.values())


def organize_node(state: KBState) -> dict:
    """过滤低分条目（< 0.6）、按 URL 去重、如有审核反馈则用 LLM 修正。

    Returns:
        {"articles": list[dict], "cost_tracker": dict}
    """
    print("[OrganizeNode] 开始整理数据")

    analyses = state.get("analyses", [])
    iteration = state.get("iteration", 0)
    feedback = state.get("review_feedback", "")
    tracker = dict(state.get("cost_tracker", {}))

    # ---- 阶段 1：修正（仅 review 后且 feedback 非空时触发） ----
    if iteration > 0 and feedback.strip():
        print(f"  [OrganizeNode] 收到审核反馈（iteration={iteration}），执行 LLM 修正")
        analyses, tracker = _revise_analyses(analyses, feedback, tracker)

    # ---- 阶段 2：过滤低分 ----
    filtered = _filter_by_rating(analyses)
    print(f"  [OrganizeNode] 过滤前 {len(analyses)} 条，过滤后 {len(filtered)} 条")

    # ---- 阶段 3：URL 去重 ----
    articles = _deduplicate_by_url(filtered)
    print(f"  [OrganizeNode] 去重后剩余 {len(articles)} 条")

    return {"articles": articles, "cost_tracker": tracker}
