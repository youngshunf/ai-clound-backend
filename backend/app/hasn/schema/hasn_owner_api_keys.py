from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnOwnerApiKeysSchemaBase(SchemaBase):
    """HASN Owner API Key 基础模型"""
    key_id: str = Field(description='Owner API Key 唯一标识')
    user_id: int | None = Field(None, description='平台用户 ID（桌面端/唤星账号场景）')
    owner_id: str = Field(description='Owner 的 hasn_id (格式: h_xxx)')
    key_name: str = Field(description='Key 名称')
    key_hash: str = Field(description='Owner API Key 的 SHA256 哈希')
    status: str = Field(description='状态 (active:生效中:green/revoked:已吊销:red/deleted:已删除:gray)')
    scopes: dict = Field(description='授权 scopes JSON')
    bound_node_id: str | None = Field(None, description='绑定 Node ID（可为空）')
    expires_at: datetime | None = Field(None, description='过期时间')
    last_used_at: datetime | None = Field(None, description='最后使用时间')
    revoked_at: datetime | None = Field(None, description='吊销时间')
    revoke_reason: str | None = Field(None, description='吊销原因 (manual_revoke:手动吊销:orange/credential_rotated:凭据轮换:blue/policy_violation:策略违规:red)')


class CreateHasnOwnerApiKeysParam(HasnOwnerApiKeysSchemaBase):
    """创建HASN Owner API Key 参数"""


class UpdateHasnOwnerApiKeysParam(HasnOwnerApiKeysSchemaBase):
    """更新HASN Owner API Key 参数"""


class DeleteHasnOwnerApiKeysParam(SchemaBase):
    """删除HASN Owner API Key 参数"""

    pks: list[int] = Field(description='HASN Owner API Key  ID 列表')


class GetHasnOwnerApiKeysDetail(HasnOwnerApiKeysSchemaBase):
    """HASN Owner API Key 详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
