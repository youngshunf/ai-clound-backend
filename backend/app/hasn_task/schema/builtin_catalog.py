"""内置任务目录 Pydantic 模型（hasn_task 应用）。"""

from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnBuiltinTaskCatalogSchemaBase(SchemaBase):
    """HASN 官方内置任务目录（云端权威）基础模型"""
    builtin_key: str = Field(description='内置任务全局唯一键（如 daily_briefing）')
    name: str = Field(description='任务名称')
    description: str | None = Field(None, description='任务说明')
    schedule_type: str = Field(description='调度类型 (cron:Cron表达式:blue/interval:间隔:green/once:单次:gray)')
    schedule_config: dict = Field(description='调度配置 JSONB（cron 用 {"expr":"0 8 * * *"}）')
    skill_bundle: str | None = Field(None, description='执行技能包（如 huanxing/workbench-briefing）')
    system_prompt: str | None = Field(None, description='系统提示词（约束输出统一格式）')
    enabled: bool = Field(description='全局上/下线开关')
    min_node_version: str | None = Field(None, description='要求的最低客户端版本（可空）')
    revision: int = Field(description='目录版本号（变化时 daemon 重拉）')


class CreateHasnBuiltinTaskCatalogParam(HasnBuiltinTaskCatalogSchemaBase):
    """创建HASN 官方内置任务目录（云端权威）参数"""


class UpdateHasnBuiltinTaskCatalogParam(HasnBuiltinTaskCatalogSchemaBase):
    """更新HASN 官方内置任务目录（云端权威）参数"""


class DeleteHasnBuiltinTaskCatalogParam(SchemaBase):
    """删除HASN 官方内置任务目录（云端权威）参数"""

    pks: list[int] = Field(description='HASN 官方内置任务目录（云端权威） ID 列表')


class GetHasnBuiltinTaskCatalogDetail(HasnBuiltinTaskCatalogSchemaBase):
    """HASN 官方内置任务目录（云端权威）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None


class BuiltinTaskItem(SchemaBase):
    """内置任务目录条目（供 daemon 拉取播种；不含 id/时间戳等内部字段）。"""

    model_config = ConfigDict(from_attributes=True)

    builtin_key: str = Field(description='内置任务全局唯一键')
    name: str = Field(description='任务名称')
    description: str | None = Field(None, description='任务说明')
    schedule_type: str = Field(description='调度类型 cron/interval/once')
    schedule_config: dict = Field(description='调度配置 JSONB')
    skill_bundle: str | None = Field(None, description='执行技能包')
    system_prompt: str | None = Field(None, description='系统提示词')
    min_node_version: str | None = Field(None, description='最低客户端版本')
    revision: int = Field(description='该条目的版本号')


class BuiltinTaskCatalogResponse(SchemaBase):
    """内置任务目录拉取响应：启用条目 + 聚合版本（任一条目增删改/上下线都会变）。"""

    items: list[BuiltinTaskItem] = Field(description='启用中的内置任务目录条目')
    catalog_revision: int = Field(description='聚合版本号（启用条目 revision 之和，变化即重拉）')
