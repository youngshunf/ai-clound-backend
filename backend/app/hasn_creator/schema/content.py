from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ContentSchemaBase(SchemaBase):
    """内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核基础模型"""
    content_no: str = Field(description='None')
    project_id: int = Field(description='None')
    user_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    enterprise_id: int | None = Field(None, description='None')
    assignee: str | None = Field(None, description='None')
    created_by_agent_id: str | None = Field(None, description='创作分身 hasn_id（审计）')
    title: str = Field(description='None')
    status: str = Field(description='状态 (idea:选题:gray/researching:调研中:blue/drafting:创作中:cyan/reviewing:待审核:orange/ready:待发布:purple/published:已发布:green/analyzing:数据跟踪:teal/completed:已复盘:green/archived:已归档:gray)')
    content_tracks: str = Field(description='形态轨道（可多值逗号）article:图文/tweet:推文/video:短视频脚本')
    pipeline_mode: str | None = Field(None, description='本篇自主度 (manual:手动:gray/semi-auto:半自动:blue/auto:自动:green)（缺省继承 project）')
    target_platforms: dict = Field(description='None')
    topic_id: int | None = Field(None, description='来源选题（topic.id 逻辑引用）')
    viral_pattern_id: int | None = Field(None, description='套用爆款模式（viral_pattern.id 逻辑引用）')
    playbook_id: int | None = Field(None, description='None')
    review_status: str | None = Field(None, description='审核状态 (pending:待审:orange/approved:通过:green/rejected:打回:red)')
    review_note: str | None = Field(None, description='主人审核意见（打回时进下次教训）')
    reviewer_user_id: int | None = Field(None, description='None')
    reviewed_at: datetime | None = Field(None, description='None')
    metadata_json: dict = Field(description='None')


class CreateContentParam(ContentSchemaBase):
    """创建内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核参数"""


class UpdateContentParam(ContentSchemaBase):
    """更新内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核参数"""


class DeleteContentParam(SchemaBase):
    """删除内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核参数"""

    pks: list[int] = Field(description='内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核 ID 列表')


class GetContentDetail(ContentSchemaBase):
    """内容主体（按画像创作，归 project）；状态机 + 多形态轨道 + 自主度 + 审核详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
