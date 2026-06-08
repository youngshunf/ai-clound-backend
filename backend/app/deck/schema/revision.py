from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class RevisionSchemaBase(SchemaBase):
    """演示文稿版本快照（云端权威历史）基础模型"""
    deck_id: int = Field(description='所属 deck（引用 deck.deck.id，bigint）')
    owner_id: str = Field(description='归属 owner HASN ID（owner 隔离键）')
    seq: int = Field(description='版本序（单调递增；(deck_id, seq) 唯一）')
    label: str = Field(description='来源标签 generated/edited/imported/manual/reverted')
    snapshot: dict = Field(description='deck + pages 完整快照（JSON；大 deck 可只存基线+diff）')
    deleted_time: datetime | None = Field(None, description='软删时间（非空=已删）')


class CreateRevisionParam(RevisionSchemaBase):
    """创建演示文稿版本快照（云端权威历史）参数"""


class UpdateRevisionParam(RevisionSchemaBase):
    """更新演示文稿版本快照（云端权威历史）参数"""


class DeleteRevisionParam(SchemaBase):
    """删除演示文稿版本快照（云端权威历史）参数"""

    pks: list[int] = Field(description='演示文稿版本快照（云端权威历史） ID 列表')


class GetRevisionDetail(RevisionSchemaBase):
    """演示文稿版本快照（云端权威历史）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
