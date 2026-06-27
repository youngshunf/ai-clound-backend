"""云端 MCP 面按分身运行位置（runtime_location）的工具可见性收口（TOOLMIG2-P4）。

本地（device-hosted）分身的 runtime 会**同时**挂载本地 daemon MCP 面（hasn-local）与
云端 MCP 面（hasn-cloud）。deck/task/workflow 这三域在本地面有本地优先引擎（Rust
DeckBroker/TaskBroker/WorkflowBroker，写本地 SQLite、可离线、与 WebUI HTTP 同源），
而 TOOLMIG2-P1/P2/P3 又在云端面补齐了同名 platform 工具——若不收口，本地分身会在两个
面各看到一份 `hasn.deck.create` 等，造成「同一分身两个同义工具、可能写到不同存储」。

故：对 **runtime_location == 'local'** 的分身，在云端面**隐藏并拒绝**这三域（其用本地面
那份）；云端（cloud-hosted）/ remote 分身无本地面 → 照常可见（云端是其唯一来源）。

设计取舍（福仔 2026-06-27 选 B：云端面对本地分身隐藏，不删本地优先基建、不改写路径）：
- **仅** runtime_location 精确等于 'local' 触发隐藏；空 / 未知 / 'cloud' / 'remote' 一律
  按可见处理——宁可对极少数误配的本地分身多显示一份，也绝不误伤云端分身（让其调不到）。
- 该收口同时作用于「发现面」（tool.search/list 经 `_can_discover`）与「执行面」
  （server.call_tool 重入式守卫，连 hasn.cloud.tool.call 透传一并拦住）。
"""

from __future__ import annotations

# 本地面已承载本地优先引擎的域：对本地分身在云端面隐藏（其余云端域照常可见）。
LOCAL_HOSTED_HIDDEN_NAMESPACES: frozenset[str] = frozenset(
    {
        'hasn.deck',
        'hasn.task',
        'hasn.workflow',
    }
)

# 触发隐藏的运行位置（精确匹配；其余值一律视为云端可达，不隐藏）。
LOCAL_RUNTIME_LOCATION = 'local'


def _namespace_of(tool_name: str) -> str:
    """从工具全名取域命名空间（与 ToolDirectoryService._fallback_namespace 同口径）。"""
    parts = tool_name.split('.')
    if len(parts) < 2:
        return tool_name
    return '.'.join(parts[:2])


def is_namespace_hidden_for_runtime(namespace: str, runtime_location: str | None) -> bool:
    """本地分身在云端面是否应隐藏该 namespace（deck/task/workflow 三域）。"""
    if (runtime_location or '').strip().lower() != LOCAL_RUNTIME_LOCATION:
        return False
    return namespace in LOCAL_HOSTED_HIDDEN_NAMESPACES


def is_tool_hidden_for_runtime(tool_name: str, runtime_location: str | None) -> bool:
    """便捷重载：按工具全名判定（内部取其 namespace）。"""
    return is_namespace_hidden_for_runtime(_namespace_of(tool_name), runtime_location)
