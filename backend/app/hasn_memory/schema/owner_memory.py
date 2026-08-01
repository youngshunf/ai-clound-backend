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
    """记忆贡献结果。

    **doc19 §10（2026-07-31）**：云端内联合并已退役，贡献只入流、不再当场并入 USER.md。
    旧字段 ``merged`` / ``version`` / ``merge_deferred`` / ``merge_error`` 一并删除——它们描述的
    是「云端这次合并成没成」，那个动作已经不存在，留着就是在描述一个不发生的机制（零 fake）。

    显式承认的体验回退：贡献进 USER.md 由「即时」变为「最长至下次整理」（主脑离线更久）。
    调用方/分身必须据 ``merge_note`` 如实告知主人，**禁止**说「已合并」或编造「后台异步合并完成」。
    """

    accepted: bool = Field(description='是否已接收为待合并贡献')
    contribution_id: int | None = Field(None, description='贡献 ID（accepted=False 时为 None）')
    pending_merge: bool = Field(
        default=False, description='已收录、等待主脑下次整理时并入（accepted=True 时恒为 True）'
    )
    merge_note: str = Field(default='', description='面向主人的如实说明（分身照此转述，别加工）')
    owner_memory_version: int = Field(default=0, description='当前合并态版本（本次调用不会改变它）')
    reason: str | None = Field(None, description='未接收的原因（如 empty_content）')


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
