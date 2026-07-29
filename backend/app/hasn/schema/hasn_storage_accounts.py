from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnStorageAccountsSchemaBase(SchemaBase):
    """用户云存储账户投影基础模型"""
    owner_hasn_id: str = Field(description='所属主人 hasn_id')
    quota_bytes: int = Field(description='当前生效配额字节数')
    used_bytes: int = Field(description='已确认计费对象字节数')
    reserved_bytes: int = Field(description='上传中预占字节数')
    quota_source: str = Field(description='配额来源 (free_policy:免费政策:blue/subscription:订阅合同:green/admin_override:管理覆盖:orange)')
    quota_version: str = Field(description='免费政策或合同版本')
    source_subscription_id: int | None = Field(None, description='付费合同来源')
    quota_valid_until: datetime | None = Field(None, description='当前配额有效期终点')
    state: str = Field(description='账户状态 (active:正常:green/over_quota:超额:orange/suspended:暂停:red)')


class CreateHasnStorageAccountsParam(HasnStorageAccountsSchemaBase):
    """创建用户云存储账户投影参数"""


class UpdateHasnStorageAccountsParam(HasnStorageAccountsSchemaBase):
    """更新用户云存储账户投影参数"""


class DeleteHasnStorageAccountsParam(SchemaBase):
    """删除用户云存储账户投影参数"""

    pks: list[int] = Field(description='用户云存储账户投影 ID 列表')


class GetHasnStorageAccountsDetail(HasnStorageAccountsSchemaBase):
    """用户云存储账户投影详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
