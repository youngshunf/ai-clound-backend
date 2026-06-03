from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAssetGrantsSchemaBase(SchemaBase):
    """HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）基础模型"""
    asset_id: str = Field(description='资产 ID (关联 hasn_assets.asset_id)')
    conversation_id: str | UUID = Field(description='授权作用域会话 ID (该会话参与者可读该资产)')


class CreateHasnAssetGrantsParam(HasnAssetGrantsSchemaBase):
    """创建HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）参数"""


class UpdateHasnAssetGrantsParam(HasnAssetGrantsSchemaBase):
    """更新HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）参数"""


class DeleteHasnAssetGrantsParam(SchemaBase):
    """删除HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）参数"""

    pks: list[int] = Field(description='HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权） ID 列表')


class GetHasnAssetGrantsDetail(HasnAssetGrantsSchemaBase):
    """HASN 资产授权表（私有资产按会话授予读权，关闭跨 owner 越权）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
