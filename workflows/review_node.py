"""
审核节点 — LLM 五维度评分，代码重算加权总分，控制在 analyses 上

职责:
  调用 chat_json(temperature=0.1) 让 LLM 从五个维度审核分析结果:
    1. summary_quality — 摘要质量 (25%)
    2. technical_depth — 技术深度 (25%)
    3. relevance — 相关性 (20%)
    4. originality — 原创性 (15%)
    5. formatting — 格式规范 (15%)
  代码重算加权总分（不信任模型算术），>= 7.0 通过。
  只审核前 5 条 analyses 以控制 token 消耗。
  LLM 调用失败时自动通过，不阻塞流程。

循环防护:
  每次调用递增 iteration，达 plan.max_iterations（默认 3）后强制通过。
"""

import json

from workflows.model_client import accumulate_usage, chat_json
from workflows.state import KBState

# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = "你是一个严谨的 AI 知识库审核专家。请严格按照 JSON 格式输出审核结果，确保评分一致性。"
"""system prompt，强调 JSON 输出与评分一致性"""

_USER_PROMPT = """请从以下五个维度对分析结果进行审核评分（每项 1-10 分）：

1. summary_quality（摘要质量，权重 25%）：摘要是否准确、完整、简洁地概括了仓库核心价值
2. technical_depth（技术深度，权重 25%）：分析是否体现了足够的技术理解和深度
3. relevance（相关性，权重 20%）：内容是否与当前知识库主题相关
4. originality（原创性，权重 15%）：分析是否具有原创洞察，而非通用描述
5. formatting（格式规范，权重 15%）：格式是否规范、一致

=== 当前分析结果（前 {count} 条） ===
{analyses_json}

请返回以下 JSON（严格遵循）:
{{
  "scores": {{
    "summary_quality": 1-10,
    "technical_depth": 1-10,
    "relevance": 1-10,
    "originality": 1-10,
    "formatting": 1-10
  }},
  "feedback": "中文审核反馈，指出具体改进方向"
}}

说明：
- 每项评分 1-10 分，1 为最差，10 为最优
- feedback 必须具体、可操作"""
"""user prompt，通知 LLM 五维度评分规则"""

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

WEIGHTS = {
    "summary_quality": 0.25,
    "technical_depth": 0.25,
    "relevance": 0.20,
    "originality": 0.15,
    "formatting": 0.15,
}
"""五维度权重，合计 1.0"""

PASS_THRESHOLD = 7.0
"""加权总分 >= 7.0 才算通过"""

MAX_ANALYSES = 5
"""最多审核前 5 条 analyses，控制 token 消耗"""

MAX_ITERATIONS = 3
"""最大审核轮次，达到后强制通过"""


# ---------------------------------------------------------------------------
# 节点函数
# ---------------------------------------------------------------------------


def review_node(state: KBState) -> dict:
    """LLM 五维度审核评分，代码重算加权总分。

    - 只审核 state["analyses"] 的前 5 条
    - temperature=0.1 保证评分一致性
    - 加权总分 >= 7.0 为通过
    - LLM 调用失败时自动通过，不阻塞流程

    Returns:
        {"review_passed": bool, "review_feedback": str,
         "iteration": int, "cost_tracker": dict}
    """
    print("[ReviewNode] 开始审核")

    analyses = state.get("analyses", [])
    iteration = state.get("iteration", 0) + 1
    tracker = dict(state.get("cost_tracker", {}))
    max_iter = state.get("plan", {}).get("max_iterations", MAX_ITERATIONS)

    # ---- 只审核前 MAX_ANALYSES 条 ----
    analyses_subset = analyses[:MAX_ANALYSES]

    if not analyses_subset:
        print("  [ReviewNode] 无分析数据，自动通过")
        return {
            "review_passed": True,
            "review_feedback": "自动通过（无分析数据）",
            "iteration": iteration,
            "cost_tracker": tracker,
        }

    # ---- 循环防护：已达最大轮次则强制通过 ----
    if iteration >= max_iter:
        print(
            f"  [ReviewNode] iteration={iteration} >= max_iterations={max_iter}，"
            "强制通过"
        )
        return {
            "review_passed": True,
            "review_feedback": "自动通过（已达最大审核轮次）",
            "iteration": iteration,
            "cost_tracker": tracker,
        }

    prompt = _USER_PROMPT.format(
        count=len(analyses_subset),
        analyses_json=json.dumps(analyses_subset, ensure_ascii=False, indent=2),
    )

    try:
        result, usage = chat_json(prompt, system=_SYSTEM_PROMPT, temperature=0.1)
        tracker = accumulate_usage(tracker, usage)
    except Exception as e:
        print(f"  [ReviewNode] 审核调用失败，自动通过: {e}")
        return {
            "review_passed": True,
            "review_feedback": f"自动通过（LLM 调用异常: {e}）",
            "iteration": iteration,
            "cost_tracker": tracker,
        }

    # ---- 提取五维度评分，默认 5 分 ----
    scores_raw = result.get("scores", {})
    summary_quality = float(scores_raw.get("summary_quality", 5))
    technical_depth = float(scores_raw.get("technical_depth", 5))
    relevance = float(scores_raw.get("relevance", 5))
    originality = float(scores_raw.get("originality", 5))
    formatting = float(scores_raw.get("formatting", 5))

    # 限幅到 [1, 10]
    summary_quality = max(1, min(10, summary_quality))
    technical_depth = max(1, min(10, technical_depth))
    relevance = max(1, min(10, relevance))
    originality = max(1, min(10, originality))
    formatting = max(1, min(10, formatting))

    # ---- 代码重算加权总分（不信任模型算术） ----
    weighted_score = (
        summary_quality * WEIGHTS["summary_quality"]
        + technical_depth * WEIGHTS["technical_depth"]
        + relevance * WEIGHTS["relevance"]
        + originality * WEIGHTS["originality"]
        + formatting * WEIGHTS["formatting"]
    )

    passed = weighted_score >= PASS_THRESHOLD
    feedback = result.get("feedback", "")

    print(
        f"  [ReviewNode] weighted_score={weighted_score:.3f},"
        f" passed={passed}, iteration={iteration}"
    )

    return {
        "review_passed": passed,
        "review_feedback": feedback,
        "iteration": iteration,
        "cost_tracker": tracker,
    }
