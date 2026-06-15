from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ProfileSchemaBase(SchemaBase):
    """项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）基础模型"""
    project_id: int = Field(description='None')
    user_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    enterprise_id: int | None = Field(None, description='None')
    assignee: str | None = Field(None, description='None')
    niche: str | None = Field(None, description='None')
    sub_niche: str | None = Field(None, description='None')
    persona: str | None = Field(None, description='None')
    target_audience: str | None = Field(None, description='None')
    tone: str | None = Field(None, description='调性（轻松幽默/专业严谨/温暖治愈…自由文本）')
    keywords: dict = Field(description='None')
    content_pillars: dict = Field(description='内容支柱 ["食谱教程","厨房好物","探店"]')
    posting_frequency: str | None = Field(None, description='None')
    best_posting_time: str | None = Field(None, description='None')
    style_references: dict = Field(description='None')
    taboo_topics: dict = Field(description='禁区话题（合规红线硬过滤，§12）')
    bio: str | None = Field(None, description='None')
    pillar_weights: dict = Field(description='支柱权重（进化核心）：复盘后按数据反馈调整，下次按权重选支柱')
    pillar_weights_updated_at: datetime | None = Field(None, description='None')


class CreateProfileParam(ProfileSchemaBase):
    """创建项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）参数"""


class UpdateProfileParam(ProfileSchemaBase):
    """更新项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）参数"""


class DeleteProfileParam(SchemaBase):
    """删除项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）参数"""

    pks: list[int] = Field(description='项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心） ID 列表')


class GetProfileDetail(ProfileSchemaBase):
    """项目画像（1:1 project）；定位参数 + 内容支柱权重（进化核心）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
