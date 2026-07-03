from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HermesAgentOperationSchemaBase(SchemaBase):
    """Hermes Agent 操作记录基础模型"""
    operation_id: str = Field(description='操作 ID')
    agent_id: str = Field(description='Agent 业务 ID')
    user_id: int = Field(description='用户 ID')
    operation_type: str = Field(description='操作类型 (create_agent:创建:blue/update_agent:更新:blue/delete_agent:删除:red/start_gateway:启动:green/restart_gateway:重启:orange/stop_gateway:停止:gray/bind_channel:绑定:purple/unbind_channel:解绑:orange/chat:对话:green/run:运行:cyan/sync_runtime:同步:blue)')
    operation_status: str = Field(description='操作状态 (started:已开始:blue/succeeded:成功:green/failed:失败:red/cancelled:已取消:gray)')
    idempotency_key: str | None = Field(None, description='幂等键')
    runtime_request_id: str | None = Field(None, description='Runtime 请求 ID')
    started_at: datetime = Field(description='开始时间')
    finished_at: datetime | None = Field(None, description='结束时间')
    request_summary_json: dict | None = Field(None, description='脱敏请求摘要 JSON')
    response_summary_json: dict | None = Field(None, description='脱敏响应摘要 JSON')
    error_json: dict | None = Field(None, description='错误 JSON')


class CreateHermesAgentOperationParam(HermesAgentOperationSchemaBase):
    """创建Hermes Agent 操作记录参数"""


class UpdateHermesAgentOperationParam(HermesAgentOperationSchemaBase):
    """更新Hermes Agent 操作记录参数"""


class DeleteHermesAgentOperationParam(SchemaBase):
    """删除Hermes Agent 操作记录参数"""

    pks: list[int] = Field(description='Hermes Agent 操作记录 ID 列表')


class GetHermesAgentOperationDetail(HermesAgentOperationSchemaBase):
    """Hermes Agent 操作记录详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
