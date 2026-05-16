"""
持久化节点 — 将 articles 写入 JSON 文件并维护索引

职责:
  1. 将每篇 article 写入 knowledge/articles/{sanitized_name}.json
  2. 维护 knowledge/articles/index.json 索引文件（幂等合并）

文件命名:
  仓库名经 _sanitize_filename() 净化：
    - "/" → "_"，空格 → "_"
    - 仅保留字母、数字、下划线、连字符
    - 空结果回退为 "article"

索引结构:
  {
    "articles": [{"filepath": "...", "name": "...", "url": "..."}],
    "_updated_at": "ISO 时间",
    "total_count": N
  }
"""

import json
import os
from datetime import datetime
from typing import Any

from workflows.state import KBState

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

ARTICLES_DIR = "knowledge/articles"
"""文章持久化目录"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _sanitize_filename(name: str) -> str:
    """将仓库名转为安全的文件名。

    >>> _sanitize_filename("torvalds/linux")
    'torvalds_linux'
    >>> _sanitize_filename("  foo bar  ")
    'foo_bar'
    """
    safe = name.replace("/", "_").replace(" ", "_")
    return "".join(c for c in safe if c.isalnum() or c in ("_", "-")) or "article"


# ---------------------------------------------------------------------------
# 节点函数
# ---------------------------------------------------------------------------


def save_node(state: KBState) -> dict:
    """将 articles 持久化到磁盘，并更新索引文件。

    Returns:
        {} — 纯 IO 操作，不修改 state
    """
    print("[SaveNode] 开始保存文章")

    articles = state.get("articles", [])
    if not articles:
        print("  [SaveNode] 无文章需要保存")
        return {}

    os.makedirs(ARTICLES_DIR, exist_ok=True)

    saved_files: list[dict[str, str]] = []
    for i, article in enumerate(articles):
        name = article.get("name", f"article_{i}")
        filename = _sanitize_filename(name) + ".json"
        filepath = os.path.join(ARTICLES_DIR, filename)

        article_with_meta: dict[str, Any] = {
            **article,
            "_saved_at": datetime.now().isoformat(),
        }

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(article_with_meta, f, ensure_ascii=False, indent=2)

        saved_files.append(
            {"filepath": filepath, "name": name, "url": article.get("url", "")}
        )
        print(f"  [SaveNode] 已保存: {filepath}")

    # ---- 更新 index.json（幂等，以 URL 为键） ----
    index_path = os.path.join(ARTICLES_DIR, "index.json")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            try:
                index: dict = json.load(f)
            except json.JSONDecodeError:
                index = {"articles": []}
    else:
        index = {"articles": []}

    existing_urls = {entry.get("url") for entry in index.get("articles", [])}
    for sf in saved_files:
        if sf["url"] not in existing_urls:
            index["articles"].append(sf)

    index["_updated_at"] = datetime.now().isoformat()
    index["total_count"] = len(index["articles"])

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    print(f"  [SaveNode] 索引已更新: {index_path}（共 {index['total_count']} 篇）")
    return {}
