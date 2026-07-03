from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnPlatformOperatorGrantsSchemaBase(SchemaBase):
    """平台运维授予源（Admin-only·G1 特权门）基础模型"""
    agent_hasn_id: str = Field(description='被授予的分身 hasn_id')
    scope: str = Field(description='特权 scope（精确值 diag:read:all / diag:manage 或段尾通配 ops:*，* 仅限末段）')
    granted_by: str = Field(description='操作的 Admin（审计）')
    note: str | None = Field(None, description='备注（授予理由，可空）')


class CreateHasnPlatformOperatorGrantsParam(HasnPlatformOperatorGrantsSchemaBase):
    """创建平台运维授予源（Admin-only·G1 特权门）参数

    granted_by 由后端从当前登录 Admin 的 JWT 自动填充（审计不可伪造），前端无需/不应传入。
    """

    granted_by: str = Field('', description='操作的 Admin（后端从 JWT 覆盖，前端不用传）')


class UpdateHasnPlatformOperatorGrantsParam(HasnPlatformOperatorGrantsSchemaBase):
    """更新平台运维授予源（Admin-only·G1 特权门）参数"""

    granted_by: str = Field('', description='操作的 Admin（后端从 JWT 覆盖，前端不用传）')


class BatchCreateHasnPlatformOperatorGrantsParam(SchemaBase):
    """批量授予平台运维特权参数（一次给同一分身勾选多个 scope）

    数据层仍是「一行一 (agent, scope)」——本参数只是让 Admin 一次多选、后端展开成多行幂等落库
    （已存在的 (agent, scope) 跳过）。granted_by 由后端从 JWT 覆盖，前端不用传。
    """

    agent_hasn_id: str = Field(description='被授予的分身 hasn_id')
    scopes: list[str] = Field(
        description='要授予的特权 scope 列表（一次可多选，如 [diag:read:all, diag:manage]）', min_length=1
    )
    note: str | None = Field(None, description='备注（授予理由，可空）')


class OperatorGrantOwnerOption(SchemaBase):
    """授予对象·用户（owner）下拉选项"""

    hasn_id: str = Field(description='用户 HASN 标识（h_ 前缀）')
    nickname: str = Field(description='用户昵称')


class OperatorGrantAgentOption(SchemaBase):
    """授予对象·分身下拉选项（隶属某 owner）"""

    hasn_id: str = Field(description='分身 HASN 标识（a_ 前缀）')
    display_name: str = Field(description='分身显示名')
    agent_name: str = Field(description='分身标识名')
    profession: str | None = Field(None, description='领域专家头衔（如「金融专家」，可空）')


class OperatorGrantScopeOption(SchemaBase):
    """特权 scope 下拉选项（声明驱动·只读目录）"""

    scope: str = Field(description='特权 scope key（如 diag:read:all）')
    label_zh: str = Field(description='中文名')
    risk: str = Field(description='风险等级（low/medium/high）')
    description: str = Field(description='说明')


class DeleteHasnPlatformOperatorGrantsParam(SchemaBase):
    """删除平台运维授予源（Admin-only·G1 特权门）参数"""

    pks: list[int] = Field(description='平台运维授予源（Admin-only·G1 特权门） ID 列表')


class GetHasnPlatformOperatorGrantsDetail(HasnPlatformOperatorGrantsSchemaBase):
    """平台运维授予源（Admin-only·G1 特权门）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
