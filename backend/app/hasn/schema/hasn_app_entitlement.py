from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAppEntitlementSchemaBase(SchemaBase):
    """AI-Native 应用权益（云端权威）基础模型"""
    app_id: str = Field(description='应用唯一标识')
    subject_type: str = Field(description='权益主体 (owner:个人:blue/enterprise:企业:purple)')
    subject_id: str = Field(description='主体 ID（owner=hasn_id / enterprise=enterprise_id）')
    source: str = Field(description='权益来源 (purchase:购买:green/trial:试用:orange/admin_grant:管理员授予:blue)')
    status: str = Field(description='权益状态 (active:生效:green/expired:已过期:gray/revoked:已撤销:red)')
    order_ref: str | None = Field(None, description='关联支付订单号（purchase 时）')
    granted_at: datetime = Field(description='授予时间')
    expires_at: datetime | None = Field(None, description='过期时间（null=永久买断）')


class CreateHasnAppEntitlementParam(HasnAppEntitlementSchemaBase):
    """创建AI-Native 应用权益（云端权威）参数"""


class UpdateHasnAppEntitlementParam(HasnAppEntitlementSchemaBase):
    """更新AI-Native 应用权益（云端权威）参数"""


class DeleteHasnAppEntitlementParam(SchemaBase):
    """删除AI-Native 应用权益（云端权威）参数"""

    pks: list[int] = Field(description='AI-Native 应用权益（云端权威） ID 列表')


class GetHasnAppEntitlementDetail(HasnAppEntitlementSchemaBase):
    """AI-Native 应用权益（云端权威）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
