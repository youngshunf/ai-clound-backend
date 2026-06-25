from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class MediaSchemaBase(SchemaBase):
    """素材库；配图/封面/视频/模板（私有桶引用）基础模型"""
    project_id: int = Field(description='None')
    user_id: int = Field(description='None')
    owner_scope: str = Field(description='None')
    enterprise_id: int | None = Field(None, description='None')
    assignee: str | None = Field(None, description='None')
    type: str = Field(description='类型 (image:图片:blue/video:视频:purple/audio:音频:orange/template:模板:green)')
    asset_uri: str = Field(description='私有桶引用（hasn://asset/）')
    filename: str | None = Field(None, description='None')
    file_size: int | None = Field(None, description='None')
    width: int | None = Field(None, description='None')
    height: int | None = Field(None, description='None')
    duration: int | None = Field(None, description='None')
    thumbnail_uri: str | None = Field(None, description='None')
    tags: dict = Field(description='None')
    description: str | None = Field(None, description='None')


class CreateMediaParam(MediaSchemaBase):
    """创建素材库；配图/封面/视频/模板（私有桶引用）参数"""


class UpdateMediaParam(MediaSchemaBase):
    """更新素材库；配图/封面/视频/模板（私有桶引用）参数"""


class DeleteMediaParam(SchemaBase):
    """删除素材库；配图/封面/视频/模板（私有桶引用）参数"""

    pks: list[int] = Field(description='素材库；配图/封面/视频/模板（私有桶引用） ID 列表')


class GetMediaDetail(MediaSchemaBase):
    """素材库；配图/封面/视频/模板（私有桶引用）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
