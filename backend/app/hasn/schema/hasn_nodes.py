from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnNodesSchemaBase(SchemaBase):
    """HASN Node 主基础模型"""
    node_id: str = Field(description='节点唯一标识 (格式: n_{uuid_short})')
    user_id: int | None = Field(None, description='平台用户 ID（桌面端/唤星账号场景）')
    allowed_owner_hasn_ids: list[str] | None = Field(None, description='允许绑定的 Owner 列表（NULL/空数组表示不限制）')
    node_type: str = Field(description='节点类型 (desktop:桌面端:blue/mobile:手机端:green/web:网页端:orange/cloud:云端:purple/sdk:SDK:cyan)')
    node_name: str | None = Field(None, description='节点名称')
    device_fingerprint: str | None = Field(None, description='设备指纹（用于幂等创建和识别同一设备）')
    device_platform: str | None = Field(None, description='设备平台')
    app_version: str | None = Field(None, description='接入端应用版本')
    ip_address: str | None = Field(None, description='最近一次连接的客户端 IP（v4/v6）')
    ip_location: str | None = Field(None, description='IP 归属地（GeoLite2 离线解析，缺库时留空表示未知）')
    node_info: dict = Field(description='节点信息 JSON')
    node_key_hash: str | None = Field(None, description='Node Key 的 SHA256 哈希')
    capacity: int = Field(description='最大 Agent 承载量')
    created_by_owner_id: str | None = Field(None, description='初始创建 Owner（仅审计用途）')
    last_seen_at: datetime | None = Field(None, description='最后活跃时间')
    status: str = Field(description='状态 (active:活跃:green/disabled:已禁用:orange/deleted:已删除:red)')


class CreateHasnNodesParam(HasnNodesSchemaBase):
    """创建HASN Node 主参数"""


class HasnNodesRegisterParam(SchemaBase):
    """开放API注册Node参数"""
    node_id: str = Field(description='节点设备指纹生成的唯一标识')
    node_type: str | None = Field('desktop', description='节点类型')
    node_name: str | None = Field(None, description='节点名称')
    node_info: dict | None = Field(None, description='节点信息')


class UpdateHasnNodesParam(HasnNodesSchemaBase):
    """更新HASN Node 主参数"""


class DeleteHasnNodesParam(SchemaBase):
    """删除HASN Node 主参数"""

    pks: list[int] = Field(description='HASN Node 主 ID 列表')


class GetHasnNodesDetail(HasnNodesSchemaBase):
    """HASN Node 主详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
