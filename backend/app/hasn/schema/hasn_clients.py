from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnClientsSchemaBase(SchemaBase):
    """HASN 客户端设备基础模型"""
    client_id: str = Field(description='客户端唯一标识 (格式: c_{uuid_short})')
    user_hasn_id: str = Field(description='所属 Human 的 hasn_id（格式: h_xxx）')
    client_type: str = Field(description='客户端类型 (desktop:桌面端:blue/mobile:手机端:green/web:网页端:orange/cloud:云端:purple)')
    device_name: str | None = Field(None, description='设备名称')
    device_info: dict = Field(description='设备信息 (JSONB)')
    last_seen_at: datetime | None = Field(None, description='最后活跃时间')
    status: str = Field(description='状态 (active:活跃:green/disabled:已禁用:orange/deleted:已删除:red)')


class CreateHasnClientsParam(HasnClientsSchemaBase):
    """创建HASN 客户端设备参数"""


class UpdateHasnClientsParam(HasnClientsSchemaBase):
    """更新HASN 客户端设备参数"""


class DeleteHasnClientsParam(SchemaBase):
    """删除HASN 客户端设备参数"""

    pks: list[int] = Field(description='HASN 客户端设备 ID 列表')


class GetHasnClientsDetail(HasnClientsSchemaBase):
    """HASN 客户端设备详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
