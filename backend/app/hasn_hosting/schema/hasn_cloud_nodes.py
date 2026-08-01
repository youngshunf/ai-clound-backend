from datetime import datetime
from uuid import UUID
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnCloudNodesSchemaBase(SchemaBase):
    """云端托管节点状态基础模型"""
    node_id: str = Field(description='对应 hasn_nodes.node_id')
    user_id: int = Field(description='平台用户 ID')
    owner_hasn_id: str = Field(description='主人 HASN ID')
    host: str = Field(description='承载宿主标识（MVP 单宿主也必须落值）')
    container_ref: str | None = Field(None, description='hosting-agent 侧容器标识')
    status: str = Field(description='状态 (provisioning:创建中:blue/starting:启动中:cyan/online:在线:green/stopped:已停止:gray/updating:更新中:orange/failed:失败:red/deleting:删除中:orange/deleted:已删除:gray)')
    failure_reason: str | None = Field(None, description='失败原因码（subscription_invalid/authorization_code_expired/authorization_code_consumed/credential_invalid/resource_exhausted/image_pull_failed/container_crashed/daemon_not_online/internal_error）')
    failure_detail: str | None = Field(None, description='人可读失败详情')
    image_version: str | None = Field(None, description='镜像版本号')
    image_digest: str | None = Field(None, description='镜像 digest（以 digest 为准，不信 tag）')
    credential_session_uuid: str | None = Field(None, description='设备凭据所在 JWT session，用于单独吊销')
    retain_until: datetime | None = Field(None, description='订阅到期后的数据保留截止时刻')
    last_backup_at: datetime | None = Field(None, description='最近一次卷备份时刻，NULL 表示尚无备份')
    online_since: datetime | None = Field(None, description='本次上线起始时刻')


class CreateHasnCloudNodesParam(HasnCloudNodesSchemaBase):
    """创建云端托管节点状态参数"""


class UpdateHasnCloudNodesParam(HasnCloudNodesSchemaBase):
    """更新云端托管节点状态参数"""


class DeleteHasnCloudNodesParam(SchemaBase):
    """删除云端托管节点状态参数"""

    pks: list[UUID] = Field(description='云端托管节点状态 ID 列表')


class GetHasnCloudNodesDetail(HasnCloudNodesSchemaBase):
    """云端托管节点状态详情"""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    created_time: datetime
    updated_time: datetime | None = None
