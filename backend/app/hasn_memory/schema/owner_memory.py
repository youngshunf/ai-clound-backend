"""Owner 记忆 DTO（USER.md 合并线，ADR 2026-05-30）。

ADR-15 收编：原定义在 `app/hasn/schema/hasn_agents.py`，随 owner_memory 模型/service/api
迁入 `app/hasn_memory`。`hasn_agents.py` 改为从此处 re-export 兼容既有 importer
（如 agent 侧 `hasn_agent_profile.py`）。
"""

from pydantic import Field

from backend.common.schema import SchemaBase


class OwnerMemoryResponse(SchemaBase):
    """下发给 Agent 的当前 owner 记忆。"""

    content: str | None = Field(None, description='当前 owner 记忆（合并后的 USER.md）')
    version: int = Field(default=0, description='记忆版本（0 表示尚无合并记忆）')
    owner_edited: bool = Field(default=False, description='主人是否在上一轮合并后直接编辑过 USER.md')
