from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class PageSchemaBase(SchemaBase):
    """演示文稿幻灯片（云端权威）基础模型"""
    deck_id: int = Field(description='所属 deck（引用 deck.deck.id，bigint）')
    owner_id: str = Field(description='归属 owner HASN ID（owner 隔离键，冗余自 deck）')
    position: int = Field(description='页序（0 起，重排改此值；未删页内 (deck_id, position) 唯一）')
    title: str = Field(description='页标题（来自 outline，便于侧栏/缩略列表）')
    html: str = Field(description='单页 HTML（自包含文档或片段，见渲染契约）')
    notes: str | None = Field(None, description='演讲者备注（可空）')
    layout_intent: str | None = Field(None, description='版式意图（冗余自 outline，如 cover/data-focus/comparison）')
    status: str = Field(description='状态 empty/generating/generated/edited/failed')
    render_state: dict | None = Field(None, description='渲染/校验结果缓存（JSON）')
    thumb_asset_id: str | None = Field(None, description='该页缩略图资产 id（预览/列表，可空）')
    rev: int = Field(description='单调版本（乐观并发 + 同步水位）')
    deleted_time: datetime | None = Field(None, description='软删时间（非空=已删）')


class CreatePageParam(PageSchemaBase):
    """创建演示文稿幻灯片（云端权威）参数"""


class UpdatePageParam(PageSchemaBase):
    """更新演示文稿幻灯片（云端权威）参数"""


class DeletePageParam(SchemaBase):
    """删除演示文稿幻灯片（云端权威）参数"""

    pks: list[int] = Field(description='演示文稿幻灯片（云端权威） ID 列表')


class GetPageDetail(PageSchemaBase):
    """演示文稿幻灯片（云端权威）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
