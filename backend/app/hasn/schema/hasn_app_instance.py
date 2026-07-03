from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAppInstanceSchemaBase(SchemaBase):
    """AI-Native 应用实例（应用的一份后端部署：公共默认 + 企业可 override）基础模型"""
    app_id: str = Field(description='应用 ID (关联 hasn_ai_native_app_manifest.app_id)')
    scope: str = Field(description='作用域 (public:公共:blue/enterprise:企业:purple)')
    enterprise_id: int | None = Field(None, description='企业 ID (scope=enterprise 必填；public 为 null)')
    endpoint: str | None = Field(None, description='实例后端基地址 (gateway_internal 可空=云端自身)')
    transport_default: str = Field(description='默认传输 (gateway_internal:就地:gray/cloud_relay:云端转发:blue/daemon_direct:本地直连:green/frontend_direct:前端直连:orange)')
    credential_ref: str | None = Field(None, description='接入凭据引用 (指向加密凭据/KMS，不存明文)')
    status: str = Field(description='状态 (active:可用:green/pending_config:待配置:orange/disabled:停用:gray)')
    config: dict = Field(description='实例配置')


class CreateHasnAppInstanceParam(HasnAppInstanceSchemaBase):
    """创建AI-Native 应用实例（应用的一份后端部署：公共默认 + 企业可 override）参数"""


class UpdateHasnAppInstanceParam(HasnAppInstanceSchemaBase):
    """更新AI-Native 应用实例（应用的一份后端部署：公共默认 + 企业可 override）参数"""


class DeleteHasnAppInstanceParam(SchemaBase):
    """删除AI-Native 应用实例（应用的一份后端部署：公共默认 + 企业可 override）参数"""

    pks: list[int] = Field(description='AI-Native 应用实例（应用的一份后端部署：公共默认 + 企业可 override） ID 列表')


class GetHasnAppInstanceDetail(HasnAppInstanceSchemaBase):
    """AI-Native 应用实例（应用的一份后端部署：公共默认 + 企业可 override）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
