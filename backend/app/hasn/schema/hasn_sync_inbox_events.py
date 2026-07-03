from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnSyncInboxEventsSchemaBase(SchemaBase):
    """HASN 客户端上行 outbox 幂等/冲突基础模型"""
    client_event_id: str = Field(description='客户端事件 ID')
    owner_id: str = Field(description='事件所属 Owner hasn_id')
    hasn_id: str = Field(description='事件主体 hasn_id（Human 或 owned Agent）')
    node_id: str = Field(description='上报 Node ID')
    event_type: str = Field(description='事件类型 (ack:确认:green/read:已读:blue/edit:编辑:orange/recall:撤回:red/local_state:本地状态:gray)')
    payload: dict = Field(description='客户端上行载荷（不得包含 workspace/endpoint/PID/CLI args/OAuth path）')
    dedupe_key: str | None = Field(None, description='业务幂等键')
    status: str = Field(description='处理状态 (accepted:已接收:blue/applied:已应用:green/conflict:冲突:orange/rejected:已拒绝:red)')
    server_revision: int | None = Field(None, description='对应服务端 revision')
    conflict_reason: str | None = Field(None, description='冲突原因')
    received_at: datetime = Field(description='服务端接收时间')


class CreateHasnSyncInboxEventsParam(HasnSyncInboxEventsSchemaBase):
    """创建HASN 客户端上行 outbox 幂等/冲突参数"""


class UpdateHasnSyncInboxEventsParam(HasnSyncInboxEventsSchemaBase):
    """更新HASN 客户端上行 outbox 幂等/冲突参数"""


class DeleteHasnSyncInboxEventsParam(SchemaBase):
    """删除HASN 客户端上行 outbox 幂等/冲突参数"""

    pks: list[int] = Field(description='HASN 客户端上行 outbox 幂等/冲突 ID 列表')


class GetHasnSyncInboxEventsDetail(HasnSyncInboxEventsSchemaBase):
    """HASN 客户端上行 outbox 幂等/冲突详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
