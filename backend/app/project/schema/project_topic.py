"""项目私有选题 Schema 定义"""

from __future__ import annotations

import datetime as dt

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase

DATETIME_TYPES = (dt.date, dt.datetime)


class ProjectTopicBase(SchemaBase):
    title: str = Field(description="选题标题")
    potential_score: float = Field(0.0, description="选题潜力分数")
    heat_index: float = Field(0.0, description="热度指数")
    reason: str = Field("", description="推荐理由")

    keywords: list[str] = Field(default_factory=list, description="标签/关键词")
    platform_heat: dict = Field(default_factory=dict, description="平台热度分布")
    heat_sources: list = Field(default_factory=list, description="热度来源")
    trend: dict = Field(default_factory=dict, description="趋势走势")
    industry_tags: list[str] = Field(default_factory=list, description="适配行业")
    target_audience: list | dict = Field(default_factory=list, description="目标人群")
    creative_angles: list[str] = Field(default_factory=list, description="创作角度")
    content_outline: list | dict = Field(default_factory=list, description="内容结构要点")
    format_suggestions: list[str] = Field(default_factory=list, description="形式建议")
    material_clues: list | dict = Field(default_factory=list, description="素材线索")
    risk_notes: list[str] = Field(default_factory=list, description="风险点")
    source_info: dict = Field(default_factory=dict, description="来源信息")

    batch_date: dt.date | None = Field(None, description="生成批次日期")
    source_uid: str | None = Field(None, description="幂等键")
    status: int = Field(0, description="状态(0:待选 1:已采纳 2:已忽略)")


class ProjectTopicSync(ProjectTopicBase):
    uuid: str = Field(description="客户端 UUID")
    is_deleted: bool = Field(False, description="是否已删除")
    deleted_at: dt.datetime | None = Field(None, description="删除时间")


class ProjectTopicSyncBatch(SchemaBase):
    topics: list[ProjectTopicSync] = Field(description="批量同步的选题列表")


class ProjectTopicInfo(ProjectTopicBase):
    id: int
    uuid: str
    project_id: int
    user_id: int
    is_deleted: bool
    last_sync_at: dt.datetime | None
    server_version: int
    created_time: dt.datetime
    updated_time: dt.datetime | None

    model_config = ConfigDict(from_attributes=True)


ProjectTopicBase.model_rebuild()
ProjectTopicSync.model_rebuild()
ProjectTopicSyncBatch.model_rebuild()
ProjectTopicInfo.model_rebuild()
