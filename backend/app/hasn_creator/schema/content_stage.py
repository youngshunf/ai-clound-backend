from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ContentStageSchemaBase(SchemaBase):
    """阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播基础模型"""
    content_id: int = Field(description='None')
    project_id: int = Field(description='None')
    user_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    enterprise_id: int | None = Field(None, description='None')
    assignee: str | None = Field(None, description='None')
    stage: str = Field(description='阶段 (research:调研:blue/outline:大纲:cyan/first_draft:初稿:orange/final_draft:终稿:purple/cover:封面:green/storyboard:分镜:teal/voiceover:口播:violet)')
    content_text: str | None = Field(None, description='None')
    asset_refs: dict = Field(description='文件产出（封面/配图 hasn://asset/ 引用，落私有桶）')
    status: str = Field(description='状态 (draft:草稿:gray/approved:已采用:green/archived:已归档:gray)')
    version: int = Field(description='None')
    source_type: str = Field(description='来源 (ai_generated:AI生成:violet/human_edited:人工编辑:blue/imported:导入:gray)')


class CreateContentStageParam(ContentStageSchemaBase):
    """创建阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播参数"""


class UpdateContentStageParam(ContentStageSchemaBase):
    """更新阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播参数"""


class DeleteContentStageParam(SchemaBase):
    """删除阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播参数"""

    pks: list[int] = Field(description='阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播 ID 列表')


class GetContentStageDetail(ContentStageSchemaBase):
    """阶段产出；调研/大纲/初稿/终稿/封面/分镜/口播详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
