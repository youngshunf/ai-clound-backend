from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnProjectInspectionSchemaBase(SchemaBase):
    """平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）基础模型"""
    owner_id: str = Field(description='归属主人 HASN ID（owner 隔离键）')
    project_id: str | UUID = Field(description='所属平台项目云端权威 UUID')
    agent_id: str = Field(description='发布巡检建议的项目经理分身 HASN ID')
    fingerprint: str = Field(description='建议幂等指纹（同 owner/项目重放不重复插入）')
    suggestion: str = Field(description='给主人展示的巡检建议正文')
    suggested_instruction: str | None = Field(None, description='按建议派发时预填给分身的执行指令')
    status: str = Field(description='状态 (unread:未读:violet/dispatched:已派发:blue/dismissed:已忽略:gray/reminded:已提醒:amber)')
    inspected_time: datetime = Field(description='分身完成本次巡检的时间')
    handled_time: datetime | None = Field(None, description='主人处理建议的时间')
    work_session_id: str | None = Field(None, description='按建议派发后回填的工作会话 ID（逻辑引用 public.hasn_sessions）')
    plan_todo_id: int | None = Field(None, description='提醒今晚后回填的计划待办 ID（逻辑引用 hasn_plan.todo）')


class CreateHasnProjectInspectionParam(HasnProjectInspectionSchemaBase):
    """创建平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）参数"""


class UpdateHasnProjectInspectionParam(HasnProjectInspectionSchemaBase):
    """更新平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）参数"""


class DeleteHasnProjectInspectionParam(SchemaBase):
    """删除平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）参数"""

    pks: list[int] = Field(description='平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11） ID 列表')


class GetHasnProjectInspectionDetail(HasnProjectInspectionSchemaBase):
    """平台项目巡检建议（项目经理分身发布的权威建议记录，doc38 C11）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
