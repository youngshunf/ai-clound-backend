from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class AssetSchemaBase(SchemaBase):
    """演示文稿资产引用（云端权威；二进制存 public.hasn_assets）基础模型"""
    deck_id: int = Field(description='所属 deck（引用 deck.deck.id，bigint）')
    owner_id: str = Field(description='归属 owner HASN ID（owner 隔离键）')
    asset_id: str | None = Field(None, description='云端资产 id（引用 public.hasn_assets.asset_id，hasn://asset/{asset_id}）')
    kind: str = Field(description='类型 image/font/export-pptx/export-pdf/thumb')
    uri: str | None = Field(None, description='hasn://asset/{id}（序列化边界经 resolve_assets 换 CDN 签名 url）')
    mime: str | None = Field(None, description='MIME 类型')
    size: int | None = Field(None, description='字节大小')
    filename: str | None = Field(None, description='文件名')
    local_path: str | None = Field(None, description='离线兜底本地路径（未上云时；上云后可清，云端通常为空）')
    deleted_time: datetime | None = Field(None, description='软删时间（非空=已删）')


class CreateAssetParam(AssetSchemaBase):
    """创建演示文稿资产引用（云端权威；二进制存 public.hasn_assets）参数"""


class UpdateAssetParam(AssetSchemaBase):
    """更新演示文稿资产引用（云端权威；二进制存 public.hasn_assets）参数"""


class DeleteAssetParam(SchemaBase):
    """删除演示文稿资产引用（云端权威；二进制存 public.hasn_assets）参数"""

    pks: list[int] = Field(description='演示文稿资产引用（云端权威；二进制存 public.hasn_assets） ID 列表')


class GetAssetDetail(AssetSchemaBase):
    """演示文稿资产引用（云端权威；二进制存 public.hasn_assets）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
