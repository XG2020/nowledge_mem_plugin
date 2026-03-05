from pydantic import Field
from typing import List

from nekro_agent.api.plugin import ConfigBase, NekroPlugin

plugin = NekroPlugin(
    name="记忆模块（Nowledge Mem）",
    module_name="nmem_memory_plugin",
    description="通过 Nowledge Mem API 为 LLM 提供长期记忆",
    version="1.0.0",
    author="XGGM",
    url="https://github.com/XG2020/nowledge_mem_plugin",
)


@plugin.mount_config()
class PluginConfig(ConfigBase):
    NMEM_API_URL: str = Field(
        default="http://127.0.0.1:14242",
        title="Nowledge Mem API 地址",
        description="Nowledge Mem 的 API 基础地址",
    )
    NMEM_API_KEY: str = Field(
        default="",
        title="Nowledge Mem API Key",
        description="Access Mem Anywhere 模式使用的 API Key",
    )
    REQUEST_TIMEOUT_SECONDS: int = Field(
        default=20,
        title="请求超时",
        description="API 请求超时时间（秒）",
    )
    SESSION_ISOLATION: bool = Field(
        default=False,
        title="记忆会话隔离",
        description="开启后通过会话ID筛选隔离无关会话记忆（写入/读取均带 session_id）",
    )
    SEARCH_LIMIT: int = Field(
        default=10,
        title="搜索结果数量",
        description="搜索接口返回的最大结果数",
    )
    LIST_LIMIT: int = Field(
        default=100,
        title="列表分页大小",
        description="列表接口单页返回的最大数量",
    )
    LIST_MAX_PAGES: int = Field(
        default=5,
        title="列表最大页数",
        description="获取所有记忆时最多拉取的分页页数",
    )
    SOURCE: str = Field(
        default="nekro-agent",
        title="来源标识",
        description="写入 Nowledge Mem 的来源标识",
    )
    AUTO_CREATE_LABELS: bool = Field(
        default=True,
        title="自动创建标签",
        description="根据标签名称自动创建缺失的标签",
    )
    RECENT_INJECT_COUNT: int = Field(
        default=5,
        title="注入记忆条数",
        description="每次对话注入的最近记忆条数",
    )
    RECENT_INJECT_TAGS: List[str] = Field(
        default=["FACTS", "TRAITS", "PREFERENCES", "RELATIONSHIPS"],
        title="注入记忆标签",
        description="注入时筛选的记忆类型标签",
    )


def get_memory_config() -> PluginConfig:
    return plugin.get_config(PluginConfig)
