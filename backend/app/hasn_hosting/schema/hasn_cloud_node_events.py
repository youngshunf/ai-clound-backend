from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnCloudNodeEventsSchemaBase(SchemaBase):
    """云端托管节点事件流水基础模型"""
    cloud_node_id: str | UUID = Field(description='关联 hasn_cloud_nodes.id')
    node_id: str = Field(description='节点 node_id（冗余，便于按设备直查）')
    event_type: str = Field(description='事件类型 (created:已创建:blue/started:已启动:green/stopped:已停止:gray/updated:已更新:cyan/update_failed:更新失败:red/rolled_back:已回滚:orange/reauthorized:已重新授权:purple/deleted:已删除:gray/backup:已备份:green/failed:失败:red)')
    detail: dict = Field(description='事件明细 JSON')


class CreateHasnCloudNodeEventsParam(HasnCloudNodeEventsSchemaBase):
    """创建云端托管节点事件流水参数"""


class UpdateHasnCloudNodeEventsParam(HasnCloudNodeEventsSchemaBase):
    """更新云端托管节点事件流水参数"""


class DeleteHasnCloudNodeEventsParam(SchemaBase):
    """删除云端托管节点事件流水参数"""

    pks: list[UUID] = Field(description='云端托管节点事件流水 ID 列表')


class GetHasnCloudNodeEventsDetail(HasnCloudNodeEventsSchemaBase):
    """云端托管节点事件流水详情"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_time: datetime
    updated_time: datetime | None = None
