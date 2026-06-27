from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAppBetaAccessSchemaBase(SchemaBase):
    """AI-Native 应用灰度内测访问（云端权威）基础模型"""
    app_id: str = Field(description='应用唯一标识')
    subject_type: str = Field(description='访问主体 (owner:个人:blue/enterprise:企业:purple)')
    subject_id: str = Field(description='主体 ID（owner=hasn_id / enterprise=enterprise_id）')
    source: str = Field(description='来源 (invite:邀请:blue/apply:申请:orange)')
    status: str = Field(description='审批状态 (pending:待审:orange/approved:已通过:green/rejected:已拒绝:red)')
    note: str | None = Field(None, description='申请理由 / 审批备注')
    decided_by: str | None = Field(None, description='审批人（admin user id 或 hasn_id；邀请时为操作管理员）')
    decided_at: datetime | None = Field(None, description='审批 / 邀请时间')


class CreateHasnAppBetaAccessParam(HasnAppBetaAccessSchemaBase):
    """创建AI-Native 应用灰度内测访问（云端权威）参数"""


class UpdateHasnAppBetaAccessParam(HasnAppBetaAccessSchemaBase):
    """更新AI-Native 应用灰度内测访问（云端权威）参数"""


class DeleteHasnAppBetaAccessParam(SchemaBase):
    """删除AI-Native 应用灰度内测访问（云端权威）参数"""

    pks: list[int] = Field(description='AI-Native 应用灰度内测访问（云端权威） ID 列表')


class GetHasnAppBetaAccessDetail(HasnAppBetaAccessSchemaBase):
    """AI-Native 应用灰度内测访问（云端权威）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
