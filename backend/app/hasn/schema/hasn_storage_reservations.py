from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnStorageReservationsSchemaBase(SchemaBase):
    """用户云存储上传预占记录基础模型"""
    reservation_id: str = Field(description='预占稳定 ID')
    owner_hasn_id: str = Field(description='所属主人 hasn_id')
    object_id: str = Field(description='预生成物理对象 ID')
    result_asset_id: str | None = Field(None, description='成功提交后的逻辑资产 ID')
    idempotency_key: str = Field(description='主人范围内的调用幂等键')
    request_fingerprint: str | None = Field(None, description='服务端计算的请求载荷 SHA-256 指纹')
    reserved_bytes: int = Field(description='当前预占字节数')
    status: str = Field(description='预占状态 (reserved:已预占:orange/committed:已提交:green/released:已释放:gray/expired:已过期:red)')
    expires_time: datetime = Field(description='预占过期时间')


class CreateHasnStorageReservationsParam(HasnStorageReservationsSchemaBase):
    """创建用户云存储上传预占记录参数"""


class UpdateHasnStorageReservationsParam(HasnStorageReservationsSchemaBase):
    """更新用户云存储上传预占记录参数"""


class DeleteHasnStorageReservationsParam(SchemaBase):
    """删除用户云存储上传预占记录参数"""

    pks: list[int] = Field(description='用户云存储上传预占记录 ID 列表')


class GetHasnStorageReservationsDetail(HasnStorageReservationsSchemaBase):
    """用户云存储上传预占记录详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
