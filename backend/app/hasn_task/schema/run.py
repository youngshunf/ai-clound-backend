"""任务执行记录 Pydantic 模型（hasn_task 应用）。"""

from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase

TASK_RUN_STATUS_DESCRIPTION = (
    '执行状态 (pending:待执行:blue/running:执行中:orange/success:成功:green/'
    'error:失败:red/timeout:超时:orange/silent:静默:gray)'
)
PROMPT_SNAPSHOT_DESCRIPTION = '执行时的完整 prompt（包含加载的 skill bundle 和 skill 内容）'


class HasnTaskRunSchemaBase(SchemaBase):
    """任务执行记录基础模型"""

    task_id: int = Field(description='关联任务 ID')
    agent_id: str = Field(description='执行 agent ID')
    session_id: str | None = Field(None, description='任务执行逻辑 session ID')
    source_conversation_id: str | None = Field(None, description='任务来源会话 ID')
    source_message_id: str | None = Field(None, description='任务来源消息 ID')
    runtime_node_id: str | None = Field(None, description='执行的 hasn-node 节点 ID')
    status: str = Field(description=TASK_RUN_STATUS_DESCRIPTION)
    started_at: datetime | None = Field(None, description='开始执行时间')
    finished_at: datetime | None = Field(None, description='完成时间')
    duration_ms: int | None = Field(None, description='执行耗时（毫秒）')
    prompt_snapshot: str | None = Field(None, description=PROMPT_SNAPSHOT_DESCRIPTION)
    output: str | None = Field(None, description='Agent 最终输出')
    error: str | None = Field(None, description='错误信息')
    model: str | None = Field(None, description='使用的模型')
    token_usage: dict | None = Field(None, description='Token 消耗 {input_tokens, output_tokens, total_tokens}')
    created_time: datetime | None = Field(None, description='创建时间')
    updated_time: datetime | None = Field(None, description='更新时间')
    continued_from_run_id: str | None = Field(None, description='本次 run 接续自哪条 run（端云稳定 run UUID，D2）')


class CreateHasnTaskRunParam(HasnTaskRunSchemaBase):
    """创建任务执行记录参数"""


class UpdateHasnTaskRunParam(HasnTaskRunSchemaBase):
    """更新任务执行记录参数"""


class DeleteHasnTaskRunParam(SchemaBase):
    """删除任务执行记录参数"""

    pks: list[int] = Field(description='任务执行记录 ID 列表')


class GetHasnTaskRunDetail(HasnTaskRunSchemaBase):
    """任务执行记录详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
