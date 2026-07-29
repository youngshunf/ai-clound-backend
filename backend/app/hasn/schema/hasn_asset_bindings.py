from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAssetBindingsSchemaBase(SchemaBase):
    """逻辑资产与业务资源的权威反向引用基础模型"""
    binding_id: str = Field(description='绑定稳定 ID')
    owner_hasn_id: str = Field(description='资产所属主人 hasn_id')
    asset_id: str = Field(description='逻辑资产 ID')
    resource_uri: str = Field(description='引用资产的稳定资源 URI')
    role: str = Field(description='引用角色')
    status: str = Field(description='绑定状态 (active:有效:green/deleted:已删除:gray)')


class CreateHasnAssetBindingsParam(HasnAssetBindingsSchemaBase):
    """创建逻辑资产与业务资源的权威反向引用参数"""


class UpdateHasnAssetBindingsParam(HasnAssetBindingsSchemaBase):
    """更新逻辑资产与业务资源的权威反向引用参数"""


class DeleteHasnAssetBindingsParam(SchemaBase):
    """删除逻辑资产与业务资源的权威反向引用参数"""

    pks: list[int] = Field(description='逻辑资产与业务资源的权威反向引用 ID 列表')


class GetHasnAssetBindingsDetail(HasnAssetBindingsSchemaBase):
    """逻辑资产与业务资源的权威反向引用详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
