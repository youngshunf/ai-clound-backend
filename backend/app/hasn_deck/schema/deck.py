from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class DeckSchemaBase(SchemaBase):
    """演示文稿（云端权威）基础模型"""
    owner_id: str = Field(description='归属 owner HASN ID（owner 隔离键，引用 public.hasn_humans）')
    title: str = Field(description='标题')
    topic: str | None = Field(None, description='原始主题/brief（生成来源，可空）')
    status: str = Field(description='状态 draft/generating/ready/archived')
    language: str = Field(description='主语言（zh/en…，影响生成）')
    outline: dict | None = Field(None, description='大纲 OutlineItem[]（JSON）')
    design_contract: dict | None = Field(None, description='统一视觉契约 DesignContract（JSON）')
    style_profile_id: str | None = Field(None, description='引用的 StyleProfile slug（可空=自定义）')
    page_count: int = Field(description='页数冗余计数（= page 行数，便于列表）')
    cover_asset_id: str | None = Field(None, description='封面缩略图资产 id（引用 public.hasn_assets.asset_id，可空）')
    source: str = Field(description='来源 (agent:分身生成:violet/manual:手建:gray/imported:导入:blue)')
    rev: int = Field(description='单调版本（乐观并发 + 同步水位，每次写 +1）')
    deleted_time: datetime | None = Field(None, description='软删时间（非空=已删，不物理删以便同步感知）')


class CreateDeckParam(DeckSchemaBase):
    """创建演示文稿（云端权威）参数"""


class UpdateDeckParam(DeckSchemaBase):
    """更新演示文稿（云端权威）参数"""


class DeleteDeckParam(SchemaBase):
    """删除演示文稿（云端权威）参数"""

    pks: list[int] = Field(description='演示文稿（云端权威） ID 列表')


class GetDeckDetail(DeckSchemaBase):
    """演示文稿（云端权威）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
