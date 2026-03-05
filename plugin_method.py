import time
from typing import Any, Dict, List, Optional

from nekro_agent.api.schemas import AgentCtx
from nekro_agent.core import config as core_config
from nekro_agent.core import logger
from nekro_agent.models.db_chat_channel import DBChatChannel
from nekro_agent.models.db_chat_message import DBChatMessage
from nekro_agent.schemas.chat_message import ChatMessage
from nekro_agent.schemas.signal import MsgSignal
from nekro_agent.services.plugin.base import SandboxMethodType

from .nowledge_client import ensure_labels, request_json
from .output_formatter import (
    format_add_output,
    format_delete_output,
    format_export_output,
    format_get_all_output,
    format_search_output,
)
from .plugin import PluginConfig, get_memory_config, plugin
from .utils import (
    build_labels,
    coerce_type_tags,
    extract_confidence,
    extract_importance,
    extract_event_dates,
    extract_source_thread_id,
    extract_title,
    get_preset_id,
    map_unit_type,
    match_tags,
)


@plugin.mount_init_method()
async def init_plugin() -> None:
    """初始化插件"""
    logger.info("Nowledge Mem 插件已初始化")


@plugin.mount_sandbox_method(
    SandboxMethodType.BEHAVIOR,
    name="添加记忆",
    description="为指定的用户添加记忆（写入 Nowledge Mem）",
)
async def add_memory(_ctx: AgentCtx, memory: str, user_id: str, metadata: Dict[str, Any]) -> str:
    """
    用途：为指定用户新增一条记忆。该方法会自动补充 metadata 中的 user_id / agent_id，
    并根据 TYPE 自动映射 unit_type，按 user/TYPE 生成标签（如开启自动创建会创建缺失标签）。

    参数：
    - memory: str
      记忆正文内容。
    - user_id: str
      用户唯一标识（注意：应为用户ID而非 chat_key）。
    - metadata: Dict[str, Any]
      元数据字典，支持以下键：
      - TYPE: str | List[str]，必填（用于分类与标签），可选值：
        FACTS | PREFERENCES | GOALS | TRAITS | RELATIONSHIPS | EVENTS | TOPICS
      - TITLE: str，可选（记忆标题）
      - IMPORTANCE: float，可选，范围 [0,1]
      - CONFIDENCE: str|float，可选，字符串枚举 VERY_HIGH|HIGH|MEDIUM|LOW|VERY_LOW 或数值 [0,1]
      - 其他自定义键将原样写入 metadata

    返回（调用反馈）：
    - 成功：返回结构化文本，包含新增记忆的 ID、标题、创建/更新时间、标签等
    - 失败：返回 "添加记忆失败: <错误>"

    示例（英文语境）：
    - add_memory(
        "Wants to be called Bob",
        "user_123",
        {"TYPE": "FACTS", "TITLE": "Preferred Name", "CONFIDENCE": "VERY_HIGH", "IMPORTANCE": 0.7},
      )
    - add_memory(
        "Has a meeting next Thursday",
        "user_123",
        {"TYPE": "GOALS", "CONFIDENCE": "LOW"}
      )

    示例（中文语境）：
    - add_memory(
        "喜欢在周末玩游戏",
        "17295800",
        {"TYPE": "PREFERENCES", "CONFIDENCE": "MEDIUM"}
      )
    - add_memory(
        "和『张三』是同事",
        "17295800",
        {"TYPE": "RELATIONSHIPS", "CONFIDENCE": 0.8}
      )
    """
    plugin_config: PluginConfig = get_memory_config()
    tags = coerce_type_tags(metadata)
    session_id = str(_ctx.chat_key) if get_memory_config().SESSION_ISOLATION and _ctx.chat_key else None
    labels = build_labels(user_id, session_id, tags)
    title = extract_title(metadata)
    importance = extract_importance(metadata)
    confidence = extract_confidence(metadata)
    event_start, event_end = extract_event_dates(metadata)
    source_thread_id = extract_source_thread_id(metadata)
    unit_type = map_unit_type(tags)
    agent_id = await get_preset_id(_ctx)
    final_metadata = dict(metadata or {})
    final_metadata.setdefault("user_id", user_id)
    if session_id:
        final_metadata.setdefault("session_id", session_id)
    final_metadata.setdefault("agent_id", agent_id)
    body: Dict[str, Any] = {
        "content": memory,
        "source": plugin_config.SOURCE,
        "metadata": final_metadata,
    }
    if title:
        body["title"] = title
    if importance is not None:
        body["importance"] = importance
    if confidence is not None:
        body["confidence"] = confidence
    if unit_type:
        body["unit_type"] = unit_type
    if event_start:
        body["event_start"] = event_start
    if event_end:
        body["event_end"] = event_end
    if source_thread_id:
        body["source_thread_id"] = source_thread_id
    if labels:
        body["labels"] = labels
        await ensure_labels(labels)
    ok, data, err = await request_json("POST", "/memories", json_body=body)
    if not ok:
        logger.error(f"添加记忆失败: {err}")
        return f"添加记忆失败: {err}"
    msg = format_add_output(data)
    logger.info(msg)
    return msg


@plugin.mount_sandbox_method(
    SandboxMethodType.AGENT,
    name="搜索记忆",
    description="通过自然语言问句进行记忆查询，支持按标签过滤",
)
async def search_memory(_ctx: AgentCtx, query: str, user_id: str, tags: Optional[List[str]] = None) -> str:
    """
    用途：以自然语言查询的方式检索指定用户的相关记忆，可选按 TYPE 标签过滤。
    内部固定使用模式：limit=配置项 SEARCH_LIMIT、include_entities=false、mode="deep"。

    参数：
    - query: str 自然语言问题或关键词
    - user_id: str 用户ID（非 chat_key）
    - tags: Optional[List[str]] 过滤标签，可选值同 TYPE：
      FACTS | PREFERENCES | GOALS | TRAITS | RELATIONSHIPS | EVENTS | TOPICS

    返回：
    - str：适合直接展示的搜索结果文本；失败返回 "搜索失败: <错误>"；若超时将自动回退到 fast 模式并降级 limit；
      若回退仍失败，建议先运行“获取工作记忆”或“获取来源线程”，或进行健康检查。

    示例：
    - search_memory("What does he like to eat?", "17295800")
    - search_memory("Topics discussed last week", "73235808", ["TOPICS"])
    - search_memory("他的个人喜好", "12345", ["PREFERENCES", "TRAITS"])
    """
    plugin_config: PluginConfig = get_memory_config()
    # 空查询时改为列表拉取，避免服务端 400
    if not query or not str(query).strip():
        all_items: List[Dict[str, Any]] = []
        offset = 0
        pages = 0
        while pages < plugin_config.LIST_MAX_PAGES:
            ok_list, data_list, err_list = await request_json(
                "GET",
                "/memories",
                params={
                    "limit": plugin_config.LIST_LIMIT,
                    "offset": offset,
                    "state": "active",
                    "importance_min": 0.0,
                },
            )
            if not ok_list or not isinstance(data_list, dict):
                logger.error(f"列表拉取失败: {err_list}")
                break
            memories = data_list.get("memories") or []
            if not memories:
                break
            all_items.extend(memories)
            pagination = data_list.get("pagination") or {}
            total = pagination.get("total", 0)
            offset += plugin_config.LIST_LIMIT
            pages += 1
            if offset >= total:
                break
        return format_get_all_output({"memories": all_items, "pagination": {}}, tags, user_id, (str(_ctx.chat_key) if get_memory_config().SESSION_ISOLATION and _ctx.chat_key else None))

    tags_lower: Optional[List[str]] = None
    if tags:
        tags_lower = [str(t).strip().lower() for t in tags if str(t).strip()]
    ok, data, err = await request_json(
        "POST",
        "/memories/search",
        json_body={
            "query": query,
            "limit": plugin_config.SEARCH_LIMIT,
            "include_entities": False,
            "mode": "deep",
            **({"filter_labels": tags_lower} if tags_lower else {}),
        },
    )
    if not ok:
        # 回退策略：fast 模式 + 降级 limit
        if isinstance(err, str) and "timed out" in err:
            ok2, data2, err2 = await request_json(
                "POST",
                "/memories/search",
                json_body={
                    "query": query,
                    "limit": min(5, max(1, plugin_config.SEARCH_LIMIT)),
                    "include_entities": False,
                    "mode": "fast",
                    **({"filter_labels": tags_lower} if tags_lower else {}),
                },
            )
            if not ok2:
                logger.error(f"搜索超时且回退失败: {err2}")
                return f"搜索失败: {err}（已尝试回退 fast 模式仍失败）"
            data = data2
        else:
            logger.error(f"搜索记忆失败: {err}")
            return f"搜索失败: {err}"
    # 若深度搜索命中为 0，则回退到 fast 模式再试
    if isinstance(data, list) and len(data) == 0:
        ok3, data3, err3 = await request_json(
            "POST",
            "/memories/search",
            json_body={
                "query": query,
                "limit": plugin_config.SEARCH_LIMIT,
                "include_entities": False,
                "mode": "fast",
                **({"filter_labels": tags_lower} if tags_lower else {}),
            },
        )
        if ok3:
            data = data3
        else:
            # 最后兜底：列表拉取并做本地关键词匹配
            all_items2: List[Dict[str, Any]] = []
            offset2 = 0
            pages2 = 0
            q = str(query).strip().lower()
            while pages2 < plugin_config.LIST_MAX_PAGES:
                ok_list2, data_list2, err_list2 = await request_json(
                    "GET",
                    "/memories",
                    params={
                        "limit": plugin_config.LIST_LIMIT,
                        "offset": offset2,
                        "state": "active",
                        "importance_min": 0.0,
                    },
                )
                if not ok_list2 or not isinstance(data_list2, dict):
                    logger.error(f"列表拉取失败: {err_list2}")
                    break
                memories2 = data_list2.get("memories") or []
                if not memories2:
                    break
                for m in memories2:
                    if not isinstance(m, dict):
                        continue
                    md = m.get("metadata", {}) or {}
                    if str(md.get("user_id")) != str(user_id):
                        continue
                    if tags:
                        tag_upper = {t.upper() for t in tags}
                        tval = md.get("TYPE") or md.get("type")
                        match = False
                        if isinstance(tval, list):
                            match = any(str(v).upper() in tag_upper for v in tval)
                        elif tval is not None:
                            match = str(tval).upper() in tag_upper
                        if not match:
                            continue
                    title = str(m.get("title") or "").lower()
                    content = str(m.get("content") or "").lower()
                    if q in title or q in content:
                        all_items2.append(m)
                pagination2 = data_list2.get("pagination") or {}
                total2 = pagination2.get("total", 0)
                offset2 += plugin_config.LIST_LIMIT
                pages2 += 1
                if offset2 >= total2:
                    break
            return format_get_all_output({"memories": all_items2, "pagination": {}}, tags, user_id)
    return format_search_output(
        data,
        tags,
        user_id,
        (str(_ctx.chat_key) if get_memory_config().SESSION_ISOLATION and _ctx.chat_key else None),
    )


@plugin.mount_sandbox_method(
    SandboxMethodType.AGENT,
    name="获取所有记忆",
    description="获取指定用户的所有记忆，支持按标签过滤",
)
async def get_all_memory(_ctx: AgentCtx, user_id: str, tags: Optional[List[str]] = None) -> str:
    """
    用途：获取指定用户的全部记忆，可按 TYPE 标签过滤；内部自动分页拉取，返回可直接展示的文本。

    参数：
    - user_id: str 用户ID（非 chat_key）
    - tags: Optional[List[str]] 过滤标签（FACTS/PREFERENCES/GOALS/TRAITS/RELATIONSHIPS/EVENTS/TOPICS）

    返回：
    - str：结构化文本；发生异常时返回空字符串

    示例：
    - get_all_memory("17295800")
    - get_all_memory("17295800", ["PREFERENCES"])
    - get_all_memory("12345", ["FACTS", "RELATIONSHIPS"])
    """
    plugin_config: PluginConfig = get_memory_config()

    all_items: List[Dict[str, Any]] = []
    offset = 0
    pages = 0
    while pages < plugin_config.LIST_MAX_PAGES:
        ok, data, err = await request_json(
            "GET",
            "/memories",
            params={
                "limit": plugin_config.LIST_LIMIT,
                "offset": offset,
                "state": "active",
                "importance_min": 0.0,
            },
        )
        if not ok:
            logger.error(f"获取记忆失败: {err}")
            break
        if not isinstance(data, dict):
            break
        memories = data.get("memories") or []
        if not memories:
            break
        all_items.extend(memories)
        pagination = data.get("pagination") or {}
        total = pagination.get("total", 0)
        offset += plugin_config.LIST_LIMIT
        pages += 1
        if offset >= total:
            break

    return format_get_all_output({"memories": all_items, "pagination": {}}, tags, user_id, (str(_ctx.chat_key) if get_memory_config().SESSION_ISOLATION and _ctx.chat_key else None))


@plugin.mount_sandbox_method(
    SandboxMethodType.BEHAVIOR,
    name="按查询删除记忆",
    description="通过查询删除匹配的记忆（先检索再删除）",
)
async def forget_memory_by_query(
    _ctx: AgentCtx,
    user_id: str,
    query: str,
    tags: Optional[List[str]] = None,
) -> str:
    """
    用途：通过自然语言查询检索并批量删除匹配的记忆。

    参数：
    - user_id: str 用户ID（非 chat_key）
    - query: str 查询语句（为空时将改为列表拉取并过滤）
    - tags: Optional[List[str]] TYPE 过滤

    返回（调用反馈）：
    - 成功：返回 "已删除 N 条记忆"
    - 失败：返回 "搜索失败: <错误>"

    示例：
    - forget_memory_by_query("17295800", "过期计划", ["GOALS"])
    """
    plugin_config: PluginConfig = get_memory_config()
    items: List[Dict[str, Any]] = []
    if not query or not str(query).strip():
        # 改为列表拉取
        offset = 0
        pages = 0
        while pages < plugin_config.LIST_MAX_PAGES:
            ok_list, data_list, err_list = await request_json(
                "GET",
                "/memories",
                params={
                    "limit": plugin_config.LIST_LIMIT,
                    "offset": offset,
                    "state": "active",
                    "importance_min": 0.0,
                },
            )
            if not ok_list or not isinstance(data_list, dict):
                logger.error(f"列表拉取失败: {err_list}")
                break
            memories = data_list.get("memories") or []
            if not memories:
                break
            for m in memories:
                if isinstance(m, dict):
                    items.append(m)
            pagination = data_list.get("pagination") or {}
            total = pagination.get("total", 0)
            offset += plugin_config.LIST_LIMIT
            pages += 1
            if offset >= total:
                break
    else:
        ok, data, err = await request_json(
            "POST",
            "/memories/search",
            json_body={
                "query": query,
                "limit": 100,
                "include_entities": False,
                "mode": "deep",
            },
        )
        if not ok:
            logger.error(f"搜索记忆失败: {err}")
            return f"搜索失败: {err}"
        if isinstance(data, list):
            for it in data:
                mem = it.get("memory") if isinstance(it, dict) else None
                if isinstance(mem, dict):
                    items.append(mem)
        # 如果搜索结果为空，回退到列表拉取并按标题/内容包含关系过滤
        if not items:
            offset = 0
            pages = 0
            q = str(query).strip()
            while pages < plugin_config.LIST_MAX_PAGES:
                ok_list, data_list, err_list = await request_json(
                    "GET",
                    "/memories",
                    params={
                        "limit": plugin_config.LIST_LIMIT,
                        "offset": offset,
                        "state": "active",
                        "importance_min": 0.0,
                    },
                )
                if not ok_list or not isinstance(data_list, dict):
                    logger.error(f"列表拉取失败: {err_list}")
                    break
                memories = data_list.get("memories") or []
                if not memories:
                    break
                for m in memories:
                    if not isinstance(m, dict):
                        continue
                    title = str(m.get("title") or "").lower()
                    content = str(m.get("content") or "").lower()
                    if q.lower() in title or q.lower() in content:
                        items.append(m)
                pagination = data_list.get("pagination") or {}
                total = pagination.get("total", 0)
                offset += plugin_config.LIST_LIMIT
                pages += 1
                if offset >= total:
                    break
    deleted = 0
    for mem in items:
        md = mem.get("metadata", {})
        if not isinstance(md, dict):
            continue
        if str(md.get("user_id")) != str(user_id):
            continue
        if get_memory_config().SESSION_ISOLATION and _ctx.chat_key is not None:
            if str(md.get("session_id")) != str(_ctx.chat_key):
                continue
        if tags:
            tag_upper = {t.upper() for t in tags}
            tval = md.get("TYPE") or md.get("type")
            match = False
            if isinstance(tval, list):
                match = any(str(v).upper() in tag_upper for v in tval)
            elif tval is not None:
                match = str(tval).upper() in tag_upper
            if not match:
                continue
        mid = mem.get("id")
        if not mid:
            continue
        await request_json("DELETE", f"/memories/{mid}", params={"cascade_delete": True})
        deleted += 1
    return f"已删除 {deleted} 条记忆"


@plugin.mount_sandbox_method(
    SandboxMethodType.AGENT,
    name="记忆时间线",
    description="按日期范围分组列出记忆活动流",
)
async def memory_timeline(
    _ctx: AgentCtx,
    user_id: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    use_event_date: bool = True,
) -> str:
    """
    用途：按日期范围汇总指定用户的记忆，按天分组输出（事件日期或记录日期）。

    参数：
    - user_id: str 用户ID（非 chat_key）
    - date_from: Optional[str] 起始日期（YYYY 或 YYYY-MM 或 YYYY-MM-DD）
    - date_to: Optional[str] 结束日期（同上）
    - use_event_date: bool True 使用事件发生日期；False 使用记录日期

    返回（调用反馈）：
    - 文本：每日条目计数与标题/内容
    - 失败：返回 "搜索失败: <错误>"

    示例：
    - memory_timeline("17295800", "2025-01-01", "2025-01-31")
    - memory_timeline("17295800", "2025", None, True)
    """
    plugin_config: PluginConfig = get_memory_config()
    body: Dict[str, Any] = {
        "query": " ",
        "limit": 100,
        "include_entities": False,
        "mode": "deep",
    }
    if use_event_date:
        if date_from:
            body["event_date_from"] = date_from
        if date_to:
            body["event_date_to"] = date_to
    else:
        if date_from:
            body["recorded_date_from"] = date_from
        if date_to:
            body["recorded_date_to"] = date_to
    ok, data, err = await request_json("POST", "/memories/search", json_body=body)
    if not ok:
        logger.error(f"搜索记忆失败: {err}")
        return f"搜索失败: {err}"
    results: List[Dict[str, Any]] = []
    if isinstance(data, list):
        for it in data:
            mem = it.get("memory") if isinstance(it, dict) else None
            if isinstance(mem, dict):
                md = mem.get("metadata", {})
                if not isinstance(md, dict):
                    continue
                if str(md.get("user_id")) != str(user_id):
                    continue
                if get_memory_config().SESSION_ISOLATION and _ctx.chat_key is not None:
                    if str(md.get("session_id")) != str(_ctx.chat_key):
                        continue
                results.append(mem)
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for mem in results:
        t = mem.get("time") or mem.get("created_at") or ""
        day = str(t)[:10] if t else "未知日期"
        groups.setdefault(day, []).append(mem)
    days = sorted(groups.keys())
    lines: List[str] = []
    for day in days:
        lines.append(f"{day}（{len(groups[day])}）")
        for i, m in enumerate(groups[day], 1):
            title = m.get("title") or "-"
            content = m.get("content") or ""
            lines.append(f"  [{i}] {title} - {content}")
    return "\n".join(lines) if lines else "无记录"


@plugin.mount_sandbox_method(
    SandboxMethodType.AGENT,
    name="获取工作记忆",
    description="读取工作记忆每日简报（可指定日期）",
)
async def get_working_memory(_ctx: AgentCtx, date: Optional[str] = None) -> str:
    """
    用途：读取工作记忆每日简报（Working Memory）。

    参数：
    - date: Optional[str] 指定日期 YYYY-MM-DD，不传则读取今日

    返回（调用反馈）：
    - JSON 文本或结构化字符串；失败返回 "获取工作记忆失败: <错误>"

    示例：
    - get_working_memory()
    - get_working_memory("2025-03-05")
    """
    params: Dict[str, Any] = {}
    if date:
        params["date"] = date
    ok, data, err = await request_json("GET", "/agent/working-memory", params=params if params else None)
    if not ok:
        logger.error(f"获取工作记忆失败: {err}")
        return f"获取工作记忆失败: {err}"
    return format_export_output(data)


@plugin.mount_sandbox_method(
    SandboxMethodType.AGENT,
    name="健康检查",
    description="检查 Nowledge Mem 服务连通性与索引状态",
)
async def health_check(_ctx: AgentCtx) -> str:
    """
    用途：检查 Nowledge Mem 服务是否可用，并获取搜索索引与嵌入模型的状态。

    参数：
    - 无

    返回（调用反馈）：
    - 文本：包含 /health 与 /search-index/status 的关键状态信息
    - 失败：分别显示对应接口的错误信息，便于快速定位网络或服务问题

    示例：
    - health_check()
    """
    lines: List[str] = []
    ok, data, err = await request_json("GET", "/health")
    if ok:
        lines.append("健康检查: 正常")
        try:
            status = data.get("status")
            version = data.get("version")
            lines.append(f"  状态: {status}  版本: {version}")
        except Exception:
            pass
    else:
        lines.append(f"健康检查: 失败 {err}")
    ok2, data2, err2 = await request_json("GET", "/search-index/status")
    if ok2:
        try:
            available = data2.get("available")
            model_cached = data2.get("model_cached")
            model_name = data2.get("model_name")
            lines.append(f"搜索索引: available={available} model_cached={model_cached} model={model_name}")
        except Exception:
            lines.append("搜索索引: 正常")
    else:
        lines.append(f"搜索索引: 失败 {err2}")
    return "\n".join(lines)


@plugin.mount_sandbox_method(
    SandboxMethodType.AGENT,
    name="获取来源线程",
    description="根据 thread_id 获取完整来源对话，支持分页",
)
async def fetch_thread(_ctx: AgentCtx, thread_id: str, limit: Optional[int] = None, offset: int = 0) -> str:
    """
    用途：根据 thread_id 获取完整来源对话，支持分页。

    参数：
    - thread_id: str 线程 ID
    - limit: Optional[int] 条数限制
    - offset: int 偏移量

    返回（调用反馈）：
    - 文本：线程标题与消息简表；失败返回 "获取线程失败: <错误>"

    示例：
    - fetch_thread("thread_123", 50, 0)
    """
    params: Dict[str, Any] = {"offset": offset}
    if limit is not None:
        params["limit"] = limit
    ok, data, err = await request_json("GET", f"/threads/{thread_id}", params=params)
    if not ok:
        logger.error(f"获取线程失败: {err}")
        return f"获取线程失败: {err}"
    try:
        thread = data.get("thread") if isinstance(data, dict) else None
        messages = data.get("messages") if isinstance(data, dict) else None
        title = thread.get("title") if isinstance(thread, dict) else ""
        lines = [f"线程: {thread_id} {title or ''}"]
        if isinstance(messages, list):
            for i, m in enumerate(messages, 1):
                role = m.get("role", "") if isinstance(m, dict) else ""
                content = m.get("content", "") if isinstance(m, dict) else ""
                lines.append(f"  [{i}] {role}: {content}")
        return "\n".join(lines)
    except Exception:
        return format_export_output(data)


@plugin.mount_sandbox_method(
    SandboxMethodType.AGENT,
    name="搜索对话线程",
    description="按关键词搜索过去的对话",
)
async def search_threads(_ctx: AgentCtx, query: str, limit: int = 20, mode: str = "full") -> str:
    """
    用途：按关键词搜索历史对话线程。

    参数：
    - query: str 关键词
    - limit: int 返回数量（默认 20）
    - mode: str "full" 或 "suggestions"

    返回（调用反馈）：
    - 文本：匹配线程列表；失败返回 "搜索线程失败: <错误>"

    示例：
    - search_threads("讨论 X 的对话", 20, "full")
    """
    ok, data, err = await request_json(
        "GET",
        "/threads/search",
        params={"query": query, "limit": limit, "mode": mode},
    )
    if not ok:
        logger.error(f"搜索线程失败: {err}")
        return f"搜索线程失败: {err}"
    try:
        threads = data.get("threads") if isinstance(data, dict) else None
        if not isinstance(threads, list) or not threads:
            return "无匹配对话"
        lines = [f"匹配对话（{len(threads)}）"]
        for i, t in enumerate(threads, 1):
            tid = t.get("id", "-")
            title = t.get("title", "-")
            lines.append(f"  [{i}] {tid} {title}")
        return "\n".join(lines)
    except Exception:
        return format_export_output(data)


@plugin.mount_sandbox_method(
    SandboxMethodType.AGENT,
    name="导出记忆",
    description="导出指定ID的记忆内容",
)
async def export_memory(_ctx: AgentCtx, memory_id: str) -> str:
    """
    用途：导出指定记忆的内容（JSON 格式）。

    参数：
    - memory_id: str 记忆唯一ID

    返回：
    - str：JSON 序列化文本；失败时返回 "导出记忆失败: <错误>"

    示例：
    - export_memory("01J5ZQ1A8S3J6M9Y4K2B7N")
    - export_memory("3JSK76D9B837N")
    """
    ok, data, err = await request_json("GET", f"/memories/{memory_id}/export", params={"format": "json"})
    if not ok:
        logger.error(f"导出记忆失败: {err}")
        return f"导出记忆失败: {err}"
    return format_export_output(data)


@plugin.mount_sandbox_method(
    SandboxMethodType.BEHAVIOR,
    name="删除记忆",
    description="删除指定ID的记忆",
)
async def delete_memory(_ctx: AgentCtx, memory_id: str) -> str:
    """
    用途：删除指定 ID 的记忆，级联删除关系（cascade_delete=true）。

    参数：
    - memory_id: str 记忆唯一ID

    返回（调用反馈）：
    - 成功：返回 "已删除记忆 ID: <id>"
    - 失败：返回 "删除记忆失败: <错误>"

    示例：
    - delete_memory("01J5ZQ1A8S3J6M9Y4K2B7N")
    - delete_memory("3JSK76D9B837N")
    """
    ok, _, err = await request_json("DELETE", f"/memories/{memory_id}", params={"cascade_delete": True})
    if not ok:
        logger.error(f"删除记忆失败: {err}")
        return f"删除记忆失败: {err}"
    msg = format_delete_output(memory_id)
    logger.info(msg)
    return msg


async def delete_all_memory(_ctx: AgentCtx, user_id: str) -> None:
    """
    Deletes all memories for a specified user.

    Args:
        user_id (str): The associated user ID. This indicates that the memories to be deleted are related to the user. It should be the user's ID, not the chat_key.
    Returns:
        None.

    Example:
        delete_all_memory("17295800")
        delete_all_memory("")

    提示词原文
    删除指定用户的所有记忆.

    Args:
        user_id (str): 关联的用户ID。代表要删除的记忆与该用户相关，这应该是用户的ID，而不是 chat_key。
    Returns:
        None.

    Example:
        delete_all_memory("17295800")
        delete_all_memory("")
    """
    mem_text = await get_all_memory(_ctx, user_id)
    if "(无结果)" in mem_text:
        return
    plugin_config: PluginConfig = get_memory_config()
    all_items: List[Dict[str, Any]] = []
    offset = 0
    pages = 0
    while pages < plugin_config.LIST_MAX_PAGES:
        ok, data, err = await request_json(
            "GET",
            "/memories",
            params={
                "limit": plugin_config.LIST_LIMIT,
                "offset": offset,
                "state": "active",
                "importance_min": 0.0,
            },
        )
        if not ok:
            logger.error(f"获取记忆失败: {err}")
            return
        memories = data.get("memories") or []
        if not memories:
            break
        all_items.extend(memories)
        pagination = data.get("pagination") or {}
        total = pagination.get("total", 0)
        offset += plugin_config.LIST_LIMIT
        pages += 1
        if offset >= total:
            break
    for item in all_items:
        metadata = item.get("metadata", {})
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("user_id")) != str(user_id):
            continue
        if plugin_config.SESSION_ISOLATION:
            current_sid = str(metadata.get("session_id")) if metadata.get("session_id") is not None else None
            expected_sid = str(_ctx.chat_key) if _ctx.chat_key is not None else None
            if current_sid != expected_sid:
                continue
        mid = item.get("id")
        if not mid:
            continue
        await request_json("DELETE", f"/memories/{mid}", params={"cascade_delete": True})


async def reset_memory_command(_ctx: AgentCtx, chatmessage: ChatMessage, args: str):  # noqa: ARG001
    try:
        await delete_all_memory(_ctx, chatmessage.sender_id)
        await _ctx.ms.send_text(_ctx.chat_key, "已清空记忆", _ctx)
    except Exception:
        logger.error("清空记忆失败")


COMMAND_MAP = {
    "del_all_mem": reset_memory_command,
}


@plugin.mount_on_user_message()
async def on_message(_ctx: AgentCtx, chatmessage: ChatMessage) -> MsgSignal:
    msg_text = chatmessage.content_text.strip()
    if not msg_text.startswith("/"):
        return MsgSignal.CONTINUE
    parts = msg_text[1:].split()
    if not parts:
        return MsgSignal.CONTINUE
    command = parts[0].lower()
    args = " ".join(parts[1:]) if len(parts) > 1 else ""
    if command in COMMAND_MAP:
        try:
            await COMMAND_MAP[command](_ctx, chatmessage, args)
            logger.info(f"成功执行指令: {command}")
        except Exception as e:
            logger.error(f"执行指令 {command} 时发生错误: {e}")
        return MsgSignal.BLOCK_ALL
    return MsgSignal.CONTINUE


@plugin.mount_prompt_inject_method(name="nekro_plugin_memory_nowledge_prompt_inject")
async def inject_memory_prompt(_ctx: AgentCtx) -> str:
    db_chat_channel: DBChatChannel = await DBChatChannel.get_channel(
        chat_key=_ctx.chat_key,
    )
    record_sta_timestamp = int(
        time.time() - core_config.AI_CHAT_CONTEXT_EXPIRE_SECONDS,
    )
    recent_messages: List[DBChatMessage] = await (
        DBChatMessage.filter(
            send_timestamp__gte=max(
                record_sta_timestamp,
                db_chat_channel.conversation_start_time.timestamp(),
            ),
            chat_key=_ctx.from_chat_key,
        )
        .order_by("-send_timestamp")
        .limit(core_config.AI_CHAT_CONTEXT_MAX_LENGTH)
    )
    recent_messages = [
        msg for msg in recent_messages if msg.sender_id != "0" and msg.sender_id != "-1"
    ]
    user_ids = set()
    for msg in recent_messages:
        if msg.sender_id and msg.sender_id != "0" and msg.sender_id != "-1":
            user_ids.add(msg.sender_id)
    user_id_list = list(user_ids)
    memory_context = "预搜索结果为空"
    if user_id_list:
        plugin_config: PluginConfig = get_memory_config()
        memory_context = ""
        for uid in user_id_list:
            try:
                all_items: List[Dict[str, Any]] = []
                offset = 0
                pages = 0
                while pages < plugin_config.LIST_MAX_PAGES:
                    ok, data, err = await request_json(
                        "GET",
                        "/memories",
                        params={
                            "limit": plugin_config.LIST_LIMIT,
                            "offset": offset,
                            "state": "active",
                            "importance_min": 0.0,
                        },
                    )
                    if not ok or not isinstance(data, dict):
                        break
                    memories = data.get("memories") or []
                    if not memories:
                        break
                    all_items.extend(memories)
                    pagination = data.get("pagination") or {}
                    total = pagination.get("total", 0)
                    offset += plugin_config.LIST_LIMIT
                    pages += 1
                    if offset >= total:
                        break
                filtered: List[Dict[str, Any]] = []
                for item in all_items:
                    md = item.get("metadata", {})
                    if not isinstance(md, dict):
                        continue
                    if str(md.get("user_id")) != str(uid):
                        continue
                    if plugin_config.SESSION_ISOLATION and _ctx.chat_key is not None:
                        if str(md.get("session_id")) != str(_ctx.chat_key):
                            continue
                    if not match_tags(md, plugin_config.RECENT_INJECT_TAGS):
                        continue
                    filtered.append(item)
                def _key(d: Dict[str, Any]) -> float:
                    t = d.get("created_at") or d.get("time") or ""
                    return 0.0 if not t else float(time.time())  # fallback stable
                # 优先使用原顺序，这里简单切片
                selected = filtered[: plugin_config.RECENT_INJECT_COUNT]
                if not selected:
                    continue
                mem_text = format_get_all_output(
                    {"memories": selected, "pagination": {}},
                    plugin_config.RECENT_INJECT_TAGS,
                    uid,
                )
                if "(无结果)" in mem_text:
                    continue
                memory_context += f"{mem_text}\n"
                logger.info(f"为用户 {uid} 注入记忆:\n{mem_text}")
            except Exception as e:
                logger.error(f"获取用户 {uid} 记忆失败: {e}")
        if not memory_context.strip():
            memory_context = "预搜索结果为空"
    PROMPT = f"""
    这是一个用于进行记忆管理的插件,你可以通过它来存储和检索与用户相关的记忆信息.
    我们规定了以下几个元数据标签,方便你对不同的记忆进行分类
    TYPE:
        - FACTS: 适用于短期内不会改变的事实信息，例如姓名、生日、职业等.
        - PREFERENCES: 适用于用户的个人喜好，例如“喜欢古典音乐”、“讨厌吃香菜”.
        - GOALS: 适用于用户的目标或愿望，例如“想在年底前学会Python”、“计划去日本旅游”.
        - TRAITS: 适用于描述用户的性格或习惯，例如“是个乐观的人”、“有晨跑的习惯”.
        - RELATIONSHIPS: 适用于记录用户的人际关系，例如“和‘张三’是同事”、“宠物猫叫‘咪咪’”.
        - EVENTS: 适用于记录事件或里程碑，例如“上个月参加了婚礼”、“去年完成了马拉松”.
        - TOPICS: 适用于记录用户曾聊过的话题，例如“有聊过人工智能”、“曾聊过恋爱话题”.
    CONFIDENCE:
        - VERY_HIGH: 几乎可以肯定是事实，有确凿证据或由用户明确确认
        - HIGH: 有很强的证据支持，大概率是正确的
        - MEDIUM: 有一定的证据支持，但仍需进一步验证
        - LOW: 不太可能，但仍有微小可能性
        - VERY_LOW: 纯属猜测或已被证伪
    以下是在使用记忆插件时需要注意的内容:
    在使用记忆模块进行记忆存取操作时（例如 add_memory、search_memory），最佳实践是将这些操作放在代码的末尾处理，尤其是在调用 send_msg_text 或 send_msg_file 等函数之后
    在聊天中主动分析聊天记录来提取有关用户的信息,使用add_memory进行存储并使用以上各种元数据标签进行分类
    这里是可以用于对话的记忆:
    {memory_context}
    如果上述内容不包含所需记忆，你可以调用 search_memory 进行检索。
    """
    return PROMPT


@plugin.mount_cleanup_method()
async def clean_up() -> None:
    logger.info("Nowledge Mem 插件清理完成")
