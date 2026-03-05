# Nowledge Mem 插件（nmem_memory_plugin）

为 LLM 提供长期记忆的插件，通过本地 Nowledge Mem API（默认 `http://127.0.0.1:14242`）实现“存—取—删—导出—线程—时间线—工作记忆”等能力，并内置搜索容错与会话隔离的可选支持。

## 功能特性

- 记忆写入与标签化：支持 TYPE→unit_type、TITLE、IMPORTANCE、CONFIDENCE、事件日期（event_start/event_end）、来源线程（source_thread_id）
- 记忆搜索（多级回退）：
  - 空查询自动转为“列表拉取 + 本地过滤”
  - 深度搜索无结果 → 自动回退 fast 模式 → 仍无结果 → 列表兜底匹配（标题/内容包含）
  - 搜索无结果会给出“换词/查看全部”的引导提示
- 列出全部记忆（分页聚合）与导出/删除
- 按查询删除（先检索再批量删除，兜底列表匹配）
- 时间线（按日期范围与天分组）
- 工作记忆每日简报读取
- 线程获取与搜索
- 会话隔离（可开关）：开启后写入带 session_id，读/搜/删/注入均按 user_id+session_id 过滤
- 自动注入最近记忆：每轮对话按用户注入最近 N 条重点记忆（可配置标签和条数）
- 网络容错：请求超时自动重试，搜索超时回退 fast 模式并降级 limit

## 快速开始

1. 启动 Nowledge Mem 服务（本地或远程），确保可访问 API
2. 配置本插件（见“配置项说明”）
3. 在对话中调用以下方法：
   - 写入：`add_memory`
   - 搜索：`search_memory`
   - 获取全部：`get_all_memory`
   - 删除：`delete_memory` 或 `forget_memory_by_query`
   - 导出：`export_memory`
   - 时间线：`memory_timeline`
   - 工作记忆：`get_working_memory`
   - 线程：`fetch_thread`、`search_threads`
   - 健康检查：`health_check`

## 配置项说明（PluginConfig）

- `NMEM_API_URL`（string，默认 `http://127.0.0.1:14242`）：Nowledge Mem API 基础地址
- `NMEM_API_KEY`（string，默认空）：远程访问时可配 API Key（将写入 `Authorization` 与 `X-API-Key`）
- `REQUEST_TIMEOUT_SECONDS`（number，默认 20）：请求超时时间（秒）
- `SESSION_ISOLATION`（boolean，默认 false）：会话隔离开关。开启后：
  - 写入带 `metadata.session_id` 与标签 `session:<chat_key>`
  - 读取/搜索/删除/时间线/注入按 `user_id + session_id` 过滤
- `SEARCH_LIMIT`（number，默认 10）：搜索返回条数上限
- `LIST_LIMIT`（number，默认 100）：列表分页大小
- `LIST_MAX_PAGES`（number，默认 5）：列表最多拉取页数（防止过大开销）
- `SOURCE`（string，默认 `nekro-agent`）：写入来源标识
- `AUTO_CREATE_LABELS`（boolean，默认 true）：自动创建缺失标签
- `RECENT_INJECT_COUNT`（number，默认 5）：每次对话注入的最近记忆条数
- `RECENT_INJECT_TAGS`（string[]，默认 `["FACTS","TRAITS","PREFERENCES","RELATIONSHIPS"]`）：注入筛选的 TYPE 标签

> 说明：已移除“记忆匹配度阈值”，搜索结果不再按相似度阈值过滤，避免误伤相似命中。

## 方法与用例

以下方法均定义于 [plugin_method.py](./plugin_method.py)，注释内包含更详细的参数与返回说明，便于大模型直接照抄调用。

### 1. 添加记忆 add_memory

- 用途：为指定用户写入记忆。会自动补充 `user_id/agent_id`；开启会话隔离时写 `session_id`
- 支持字段：
  - `TYPE`：FACTS | PREFERENCES | GOALS | TRAITS | RELATIONSHIPS | EVENTS | TOPICS（可列表）
  - `TITLE`、`IMPORTANCE(0~1)`、`CONFIDENCE(枚举或0~1)`、`EVENT_START/END`、`SOURCE_THREAD_ID`
- 示例：
  - `add_memory("喜欢打游戏", "1401668510", {"TYPE":"PREFERENCES","TITLE":"用户爱好","CONFIDENCE":"VERY_HIGH","IMPORTANCE":0.8})`

### 2. 搜索记忆 search_memory

- 用途：自然语言搜索 + 标签过滤
- 空查询：自动改为“列表拉取 + 本地过滤”
- 深度搜索无结果：回退 `fast` → 仍无 → 列表兜底（标题/内容包含）
- 示例：
  - `search_memory("打游戏", "1401668510", ["PREFERENCES"])`
  - 无结果时输出会包含“建议换词或查看全部”的提示

### 3. 获取全部记忆 get_all_memory

- 用途：分页拉取并按 `user_id` 与标签过滤，适合做总览
- 示例：`get_all_memory("1401668510", ["PREFERENCES"])`

### 4. 导出记忆 export_memory

- 用途：按 ID 导出 JSON
- 示例：`export_memory("<memory_id>")`

### 5. 删除记忆 delete_memory / 按查询删除 forget_memory_by_query

- `delete_memory("<id>")`：按 ID 删除（级联关系）
- `forget_memory_by_query(user_id, query, tags?)`：
  - 优先用搜索；搜索为空或无结果 → 列表兜底（标题/内容包含）
  - 示例：`forget_memory_by_query("1401668510","喜欢打游戏",["PREFERENCES"])`

### 6. 时间线 memory_timeline

- 用途：按日期范围分组输出每日活动（事件日期或记录日期）
- 示例：`memory_timeline("1401668510","2026-03-01","2026-03-31",True)`

### 7. 工作记忆 get_working_memory

- 用途：读取每日简报（Working Memory）
- 示例：`get_working_memory()` 或 `get_working_memory("2026-03-05")`

### 8. 线程 fetch_thread / 搜索线程 search_threads

- `fetch_thread(thread_id, limit?, offset?)`：获取线程消息
- `search_threads(query, limit=20, mode="full")`：关键词搜索历史对话

### 9. 健康检查 health_check

- 用途：检查 `/health` 与 `/search-index/status`，快速诊断连通/索引/模型状态
- 示例：`health_check()`

## 会话隔离策略

- 开启：写入 `session_id`，读/搜/删/注入均按 `user_id+session_id` 双条件过滤
- 关闭：仅按 `user_id` 过滤，历史有 `session_id` 的记录仍可被读取（不受 session 限制）
- 注意：
  - 开启隔离后写入的新记忆仅在当前会话可见；关闭隔离后跨会话共享
  - 若切回开启隔离，早期无 `session_id` 的旧记忆将不再注入/搜索到（可关闭隔离或迁移）

## 搜索容错建议

- 优先提供具体关键词与合适标签（如 `["PREFERENCES"]`）
- 空查询可用 `get_all_memory(user_id, tags?)` 做总览后再筛选
- 超时可先运行 `health_check()` 判断服务与索引状态

## 与 Nowledge Mem API 的兼容

- 仅使用文档定义的字段与端点：
  - `/memories` 增删查导出、`/memories/search` 搜索、`/threads` 线程相关、`/agent/working-memory`、`/search-index/status` 等
- 请求规范：
  - Query 布尔传 `true/false`（小写）；`None` 值不传；列表用多值展开
  - Body 字段严格遵循文档键名（不会注入自定义顶层字段）

## 开发说明

- 核心文件
  - 配置与注册：[plugin.py](./plugin.py)
  - API 客户端与容错：[nowledge_client.py](./nowledge_client.py)
  - 方法实现与注释：[plugin_method.py](./plugin_method.py)
  - 输出格式化：[output_formatter.py](./output_formatter.py)
  - 工具与标签映射：[utils.py](./utils.py)
- 代码风格
  - 严格不在日志中泄露密钥
  - 请求错误统一记录错误码与返回体

## 常见问题

- 搜索无结果但“全部记忆”能看到：
  - 搜索已自带三层回退；若仍无，请尝试更具体的关键词或直接使用 `get_all_memory`
- 空查询报 400：
  - 已修复：空查询会走列表兜底，不再直接调用 `/memories/search`
- 会话隔离生效后跨会话看不到：
  - 这是预期。关闭隔离即可按 `user_id` 全局共享，或在开启隔离时确保在同一会话写入与查询

---

如需调整默认注入条数、注入标签或隔离默认值，请在配置中修改相应项并重载插件。*** End Patch*** End Patch
*** End Patch
*** End Patch
*** End Patch
