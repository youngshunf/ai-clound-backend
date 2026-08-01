from datetime import datetime
from uuid import UUID
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnNodeAuthorizationCodesSchemaBase(SchemaBase):
    """云端节点设备授权码基础模型"""
    code_hash: str = Field(description='授权码 sha256 十六进制，明文不入库')
    user_id: int = Field(description='平台用户 ID')
    owner_hasn_id: str = Field(description='主人 HASN ID')
    node_id: str = Field(description='预分配的 hasn_nodes.node_id')
    purpose: str = Field(description='用途 (create:首次创建:blue/reauthorize:重新授权:orange)')
    expires_at: datetime = Field(description='过期时刻（签发 + 5 分钟）')
    consumed_at: datetime | None = Field(None, description='兑换时刻')
    status: str = Field(description='状态 (pending:待兑换:blue/consumed:已兑换:green/expired:已过期:orange/revoked:已作废:red)')


class CreateHasnNodeAuthorizationCodesParam(HasnNodeAuthorizationCodesSchemaBase):
    """创建云端节点设备授权码参数"""


class UpdateHasnNodeAuthorizationCodesParam(HasnNodeAuthorizationCodesSchemaBase):
    """更新云端节点设备授权码参数"""


class DeleteHasnNodeAuthorizationCodesParam(SchemaBase):
    """删除云端节点设备授权码参数"""

    pks: list[UUID] = Field(description='云端节点设备授权码 ID 列表')


class GetHasnNodeAuthorizationCodesDetail(HasnNodeAuthorizationCodesSchemaBase):
    """云端节点设备授权码详情"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_time: datetime
    updated_time: datetime | None = None
