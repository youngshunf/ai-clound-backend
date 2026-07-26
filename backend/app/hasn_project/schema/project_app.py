"""平台项目 Owner API 的严格读写契约（doc38 C2/C8）。"""

from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class _ProjectWriteBody(SchemaBase):
    """只接收项目白名单字段；字段是否传入由 ``exclude_unset`` 保留。"""

    model_config = ConfigDict(extra='forbid')

    # 允许显式 null 到达统一 service：创建时 name 仍由 service 给出业务错误；更新时
    # goal/cover_asset_uri/bound_agent_id 的 null 表示清空，省略表示保持原值。
    name: str | None = Field(default=None, description='项目名')
    goal: str | None = Field(default=None, description='一句话目标；null 或空白表示清空')
    cover_asset_uri: str | None = Field(default=None, description='封面资产引用 hasn://asset/{id}；null 表示清空')
    status: str | None = Field(default=None, description='项目状态 active 或 archived')
    bound_agent_id: str | None = Field(default=None, description='主人名下默认协作分身；null 表示解绑')


class ProjectCreateBody(_ProjectWriteBody):
    """创建项目请求体。"""


class ProjectUpdateBody(_ProjectWriteBody):
    """更新项目请求体。"""


class InspectionMarkDispatchedBody(SchemaBase):
    """按建议派发完成后回填的真实工作会话。"""

    model_config = ConfigDict(extra='forbid')

    work_session_id: str = Field(min_length=1, max_length=64, description='按建议派发创建的项目工作会话 ID')


class InspectionMarkRemindedBody(SchemaBase):
    """提醒今晚创建后回填的真实计划待办。"""

    model_config = ConfigDict(extra='forbid')

    plan_todo_id: int = Field(gt=0, description='提醒今晚创建的计划待办 ID')


class ProjectLinkedApp(SchemaBase):
    """项目挂靠容器按应用聚合后的稳定展示项。"""

    app_id: str = Field(description='应用标识')
    count: int = Field(ge=0, description='该应用挂靠容器数量')


class ProjectSummary(SchemaBase):
    """项目列表卡与详情总览共用的稳定摘要。"""

    id: str = Field(description='项目云端权威 UUID')
    owner_id: str = Field(description='主人 HASN ID')
    name: str = Field(description='项目名')
    goal: str | None = Field(description='一句话目标')
    cover_asset_uri: str | None = Field(description='封面资产 URI')
    status: str = Field(description='项目状态')
    bound_agent_id: str | None = Field(description='默认协作分身')
    enterprise_id: str | None = Field(description='企业归属')
    created_time: datetime = Field(description='创建时间')
    updated_time: datetime | None = Field(description='更新时间')
    artifact_count: int = Field(ge=0, description='三路并集去重后的产物数')
    session_count: int = Field(ge=0, description='项目工作会话总数')
    active_session_count: int = Field(ge=0, description='进行中或等待主人会话数')
    agent_count: int = Field(ge=0, description='参与分身数')
    link_count: int = Field(ge=0, description='挂靠容器总数')
    milestone_done_count: int = Field(ge=0, description='已完成里程碑数')
    milestone_total_count: int = Field(ge=0, description='里程碑总数')
    agent_ids: list[str] = Field(description='默认分身、参与记录分身与会话分身的稳定去重并集')
    linked_apps: list[ProjectLinkedApp] = Field(description='按应用聚合的挂靠容器')
    last_activity_time: datetime | None = Field(description='里程碑、产物、会话和挂靠变更中的最近时间')
