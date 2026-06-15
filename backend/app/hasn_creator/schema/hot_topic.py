from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HotTopicSchemaBase(SchemaBase):
    """热榜快照（全局，去重，喂选题；可选数据源）基础模型"""
    platform_id: str = Field(description='None')
    platform_name: str | None = Field(None, description='None')
    title: str = Field(description='None')
    url: str | None = Field(None, description='None')
    rank: int = Field(description='None')
    heat_score: float = Field(description='None')
    fetch_source: str | None = Field(None, description='None')
    fetched_at: datetime | None = Field(None, description='None')
    batch_date: str = Field(description='批次（去重键：platform_id+url+batch_date）')


class CreateHotTopicParam(HotTopicSchemaBase):
    """创建热榜快照（全局，去重，喂选题；可选数据源）参数"""


class UpdateHotTopicParam(HotTopicSchemaBase):
    """更新热榜快照（全局，去重，喂选题；可选数据源）参数"""


class DeleteHotTopicParam(SchemaBase):
    """删除热榜快照（全局，去重，喂选题；可选数据源）参数"""

    pks: list[int] = Field(description='热榜快照（全局，去重，喂选题；可选数据源） ID 列表')


class GetHotTopicDetail(HotTopicSchemaBase):
    """热榜快照（全局，去重，喂选题；可选数据源）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
