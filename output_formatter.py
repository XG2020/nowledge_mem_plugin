from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Optional

from .utils import match_tags, match_user, match_user_session


def _safe_get(d: Mapping[str, Any], key: str, default: Any = None) -> Any:
    try:
        return d.get(key, default)
    except Exception:
        return default


def _fmt_metadata(md: Optional[Mapping[str, Any]]) -> str:
    if not md:
        return "-"
    try:
        parts = [f"{k}={v}" for k, v in md.items()]
        return ", ".join(parts) if parts else "-"
    except Exception:
        return str(md)


def _coerce_list(data: Any) -> List[Dict[str, Any]]:
    if data is None:
        return []
    if isinstance(data, dict):
        if "results" in data and isinstance(data["results"], list):
            return [x for x in data["results"] if isinstance(x, dict)]
        return [data]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _group_by_user(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups = defaultdict(list)
    for item in items:
        metadata = _safe_get(item, "metadata", {})
        user_id = "-"
        if isinstance(metadata, dict):
            user_id = str(metadata.get("user_id", "-"))
        groups[user_id].append(item)
    return dict(groups)


def _fmt_entry(idx: int, item: Mapping[str, Any]) -> List[str]:
    title = _safe_get(item, "title", "-")
    content = _safe_get(item, "content", "")
    mid = _safe_get(item, "id", "-")
    created = _safe_get(item, "created_at", _safe_get(item, "time", "-"))
    updated = _safe_get(item, "updated_at", "-")
    tags = _fmt_metadata(_safe_get(item, "metadata"))
    st = _safe_get(item, "source_thread")
    st_desc = "-"
    try:
        if isinstance(st, dict):
            sid = st.get("id")
            stitle = st.get("title")
            if sid or stitle:
                st_desc = f"{sid or '-'} {stitle or ''}".strip()
    except Exception:
        st_desc = "-"
    return [
        f"  [{idx}] 记忆: {content}",
        f"       ID: {mid}",
        f"       标题: {title}",
        f"       来源线程: {st_desc}",
        f"       创建: {created}    更新: {updated}",
        f"       标签: {tags}",
    ]


def _format_grouped(items: List[Dict[str, Any]], include_scores: bool = False) -> List[str]:
    if not items:
        return []
    grouped = _group_by_user(items)
    parts = []
    for user_id, user_items in grouped.items():
        parts.append(f"用户: {user_id} ({len(user_items)} 条记忆)")
        for i, item in enumerate(user_items, 1):
            lines = _fmt_entry(i, item)
            if include_scores:
                score = _safe_get(item, "similarity_score")
                if score is not None:
                    try:
                        lines.insert(1, f"       相关度: {float(score):.4f}")
                    except Exception:
                        lines.insert(1, f"       相关度: {score}")
                reason = _safe_get(item, "relevance_reason")
                if reason:
                    lines.insert(2, f"       相关原因: {reason}")
            parts.append("\n".join(lines))
    return parts


def format_search_output(
    data: Any,
    tags: Optional[List[str]],
    user_id: str,
    session_id: Optional[str] = None,
) -> str:
    items = _coerce_list(data)
    normalized = []
    for item in items:
        memory = _safe_get(item, "memory")
        if not isinstance(memory, dict):
            continue
        metadata = _safe_get(memory, "metadata", {})
        if session_id is None:
            if not match_user(metadata, user_id):
                continue
        else:
            if not match_user_session(metadata, user_id, session_id):
                continue
        if not match_tags(metadata, tags):
            continue
        score = _safe_get(item, "similarity_score")
        merged = dict(memory)
        merged["similarity_score"] = score
        merged["relevance_reason"] = _safe_get(item, "relevance_reason")
        normalized.append(merged)
    filter_parts = []
    if tags:
        filter_parts.append(f"标签: {', '.join(tags)}")
    filter_desc = f"（筛选: {' | '.join(filter_parts)}）" if filter_parts else ""
    header = f"搜索结果{filter_desc}（{len(normalized)} 条）"
    if not normalized:
        suggest_tags = f", {', '.join(tags)}" if tags else ""
        hint_lines = [
            "(无结果)",
            f"尝试更换同义词，或调用 get_all_memory(\"{user_id}\"{', ' + str(tags) if tags else ''}) 查看全部后再筛选",
        ]
        return header + "\n" + "\n".join(hint_lines)
    parts = [header]
    parts.extend(_format_grouped(normalized, include_scores=True))
    return "\n\n".join(parts)


def format_get_all_output(
    data: Any,
    tags: Optional[List[str]],
    user_id: str,
    session_id: Optional[str] = None,
) -> str:
    items = []
    if isinstance(data, dict) and isinstance(data.get("memories"), list):
        items = [x for x in data.get("memories") if isinstance(x, dict)]
    else:
        items = _coerce_list(data)
    filtered = []
    for item in items:
        metadata = _safe_get(item, "metadata", {})
        if session_id is None:
            if not match_user(metadata, user_id):
                continue
        else:
            if not match_user_session(metadata, user_id, session_id):
                continue
        if not match_tags(metadata, tags):
            continue
        filtered.append(item)
    tag_filter_desc = f"（筛选: {', '.join(tags)}）" if tags else ""
    header = f"全部记忆{tag_filter_desc}（{len(filtered)} 条）"
    if not filtered:
        return header + "\n(无结果)"
    parts = [header]
    parts.extend(_format_grouped(filtered, include_scores=False))
    return "\n\n".join(parts)


def format_add_output(data: Any) -> str:
    if isinstance(data, dict) and isinstance(data.get("memory"), dict):
        memory = data["memory"]
        return "新增记忆（1 条）\n" + "\n".join(_fmt_entry(1, memory))
    items = _coerce_list(data)
    header = f"新增记忆（{len(items)} 条）"
    if not items:
        return header + "\n(无返回)"
    parts = [header]
    parts.extend(_format_grouped(items, include_scores=False))
    return "\n\n".join(parts)


def format_delete_output(memory_id: str) -> str:
    return f"已删除记忆 ID: {memory_id}"


def format_export_output(history: Any) -> str:
    try:
        return json.dumps(history, ensure_ascii=False, indent=2)
    except Exception:
        return str(history)
