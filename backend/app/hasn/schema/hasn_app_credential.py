from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAppCredentialSchemaBase(SchemaBase):
    """AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）基础模型"""
    app_id: str = Field(description='应用 ID（如 knowledge）')
    user_id: int = Field(description='用户 ID')
    app_instance_id: int = Field(description='所属应用实例 ID（hasn_app_instance.id）')
    credential_ref: str = Field(description='用户级凭据密文（key_encryption.encrypt，绝不存明文）')
    status: str = Field(description='状态 (pending:待激活:gray/active:已激活:green/revoked:已吊销:red/error:错误:orange)')
    last_error: str | None = Field(None, description='最近一次 provision/刷新错误')
    config: dict = Field(description='app 私有凭据元数据（如 ragflow_user_id/ragflow_tenant_id）')


class CreateHasnAppCredentialParam(HasnAppCredentialSchemaBase):
    """创建AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）参数"""


class UpdateHasnAppCredentialParam(HasnAppCredentialSchemaBase):
    """更新AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）参数"""


class DeleteHasnAppCredentialParam(SchemaBase):
    """删除AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）参数"""

    pks: list[int] = Field(description='AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential） ID 列表')


class GetHasnAppCredentialDetail(HasnAppCredentialSchemaBase):
    """AI-Native App 用户级接入凭据（泛化 hasn_ragflow_credential）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
