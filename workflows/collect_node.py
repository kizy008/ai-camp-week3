"""
采集节点 — 调用 GitHub Search API 搜索 AI 相关仓库

职责:
  读取 plan.strategy / plan.per_source_limit，构造 GitHub API 请求，
  将返回的 JSON 解析为结构化 sources 列表。

异常处理:
  - 网络/DNS 错误 → 返回空列表
  - JSON 解析失败 → 返回空列表
  - GitHub API 限流 → 返回空列表（日志记录）
"""

import json
import urllib.error
import urllib.parse
import urllib.request

from workflows.state import KBState

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

GITHUB_API_BASE = "https://api.github.com"
"""GitHub REST API 基础 URL"""

DEFAULT_PER_PAGE = 10
"""每页返回的仓库数量"""


# ---------------------------------------------------------------------------
# 节点函数
# ---------------------------------------------------------------------------


def collect_node(state: KBState) -> dict:
    """从 GitHub 搜索 AI 相关仓库，返回 sources 列表。

    从 plan 中读取 strategy（搜索关键词，默认 "AI machine learning"）
    和 per_source_limit（每页数量），使用 urllib 发送 HTTP GET 请求。

    Returns:
        {"sources": list[dict]} — 每个 dict 包含 id, name, url,
        description, language, stars, forks, topics, created_at, updated_at
    """
    print("[CollectNode] 开始采集 GitHub 仓库数据")

    plan = state.get("plan", {})
    strategy = plan.get("strategy", "AI Agent")
    per_page = plan.get("per_source_limit", DEFAULT_PER_PAGE)

    query = urllib.parse.quote(strategy)
    url = (
        f"{GITHUB_API_BASE}/search/repositories"
        f"?q={query}&sort=stars&order=desc&per_page={per_page}"
    )

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "AI-Knowledge-Base/1.0",
            "Accept": "application/vnd.github.v3+json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"  [CollectNode] GitHub API 请求失败: {e}")
        return {"sources": []}

    items = data.get("items", [])
    sources = [
        {
            "id": item.get("id"),
            "name": item.get("full_name", ""),
            "url": item.get("html_url", ""),
            "description": item.get("description") or "",
            "language": item.get("language") or "",
            "stars": item.get("stargazers_count", 0),
            "forks": item.get("forks_count", 0),
            "topics": item.get("topics", []),
            "created_at": item.get("created_at", ""),
            "updated_at": item.get("updated_at", ""),
        }
        for item in items
    ]

    print(f"  [CollectNode] 采集到 {len(sources)} 条仓库数据（关键词: {strategy}）")
    return {"sources": sources}
