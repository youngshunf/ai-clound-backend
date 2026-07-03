from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnNodeBindingsSchemaBase(SchemaBase):
    """HASN Node Owner Binding 租约基础模型"""
    binding_id: str = Field(description='绑定唯一标识 (格式: ob_{uuid})')
    node_id: str = Field(description='节点 ID (格式: n_{uuid_short})')
    owner_id: str = Field(description='Owner 的 hasn_id (格式: h_xxx)')
    auth_profile: str = Field(description='认证模式 (bearer_token:平台令牌:blue/owner_api_key:Owner API Key:green/mtls_bound_token:mTLS绑定令牌:purple/dpop_token:DPoP令牌:cyan)')
    scopes: dict = Field(description='授权 scopes JSON')
    status: str = Field(description='状态 (active:生效中:green/expired:已过期:orange/revoked:已吊销:red/removed:已解绑:gray)')
    bound_at: datetime = Field(description='绑定时间')
    expires_at: datetime = Field(description='过期时间')
    renewed_at: datetime | None = Field(None, description='最近续期时间')
    revoked_at: datetime | None = Field(None, description='吊销时间')
    revoke_reason: str | None = Field(None, description='吊销原因 (manual_revoke:手动吊销:orange/credential_rotated:凭据轮换:blue/policy_violation:策略违规:red)')
    last_used_at: datetime | None = Field(None, description='最后使用时间')


class CreateHasnNodeBindingsParam(HasnNodeBindingsSchemaBase):
    """创建HASN Node Owner Binding 租约参数"""


class UpdateHasnNodeBindingsParam(HasnNodeBindingsSchemaBase):
    """更新HASN Node Owner Binding 租约参数"""


class DeleteHasnNodeBindingsParam(SchemaBase):
    """删除HASN Node Owner Binding 租约参数"""

    pks: list[int] = Field(description='HASN Node Owner Binding 租约 ID 列表')


class GetHasnNodeBindingsDetail(HasnNodeBindingsSchemaBase):
    """HASN Node Owner Binding 租约详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
