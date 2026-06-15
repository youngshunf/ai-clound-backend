"""Owner 记忆 DTO（USER.md 合并线，ADR 2026-05-30）。

ADR-15 收编：原定义在 `app/hasn/schema/hasn_agents.py`，随 owner_memory 模型/service/api
迁入 `app/hasn_memory`。`hasn_agents.py` 改为从此处 re-export 兼容既有 importer
（如 agent 侧 `hasn_agent_profile.py`）。
"""

from datetime import datetime

from pydantic import Field

from backend.common.schema import SchemaBase


class MemoryContributeRequest(SchemaBase):
    """Agent 上传一条 owner 记忆观察（本地 USER.md 增量）。"""

    content: str = Field(min_length=1, description='Agent 观察到的主人信息片段')


class MemoryContributeResponse(SchemaBase):
    """记忆贡献结果（含本轮是否触发合并）。"""

    accepted: bool = Field(description='是否已接收为待合并贡献')
    merged: bool = Field(default=False, description='本次是否触发了合并')
    version: int | None = Field(None, description='合并后 owner 记忆版本（未合并则 None）')


class OwnerMemoryResponse(SchemaBase):
    """下发给 Agent 的当前 owner 记忆。"""

    content: str | None = Field(None, description='当前 owner 记忆（合并后的 USER.md）')
    version: int = Field(default=0, description='记忆版本（0 表示尚无合并记忆）')


class OwnerMemoryContributionItem(SchemaBase):
    """单条 owner 记忆贡献（owner 透明视图）。"""

    id: int = Field(description='贡献 ID')
    agent_hasn_id: str = Field(description='上传 Agent 的 hasn_id')
    content: str | None = Field(None, description='观察片段')
    status: str = Field(description='状态 (pending/merged/discarded)')
    merged_into_version: int | None = Field(None, description='合并进的 owner_memory 版本')
    created_time: datetime | None = Field(None, description='上传时间')


class OwnerMemoryContributionsResponse(SchemaBase):
    """owner 查看自己记忆的贡献流（通信对主人透明）。"""

    items: list[OwnerMemoryContributionItem] = Field(default_factory=list, description='贡献列表（按时间倒序）')
    pending_count: int = Field(default=0, description='待合并贡献数')
