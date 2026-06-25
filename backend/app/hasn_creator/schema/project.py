from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class ProjectSchemaBase(SchemaBase):
    """运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度基础模型"""
    project_no: str = Field(description='None')
    user_id: int = Field(description='None')
    owner_scope: str = Field(description='归属模式 (personal:个人:blue/enterprise:企业:purple)')
    enterprise_id: int | None = Field(None, description='企业 ID（enterprise 模式；personal 为 NULL）')
    assignee: str | None = Field(None, description='负责运营的人 hasn_id（角色裁剪键；personal=owner_hasn_id）')
    assignee_agent_id: str | None = Field(None, description='负责运营的分身 hasn_id（§8.4 主脑 re-bind）')
    name: str = Field(description='None')
    description: str | None = Field(None, description='None')
    primary_platform: str | None = Field(None, description='主平台 (xiaohongshu:小红书:red/douyin:抖音:gray/wechat_mp:公众号:green/weibo:微博:orange/bilibili:B站:cyan/zhihu:知乎:blue)')
    pipeline_mode: str = Field(description='运营自主度 (manual:手动:gray/semi-auto:半自动:blue/auto:自动:green)')
    playbook_id: int | None = Field(None, description='采用的账号打法（playbook.id 逻辑引用）')
    status: str = Field(description='状态 (active:运营中:green/paused:已暂停:orange/archived:已归档:gray)')


class CreateProjectParam(ProjectSchemaBase):
    """创建运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度参数"""


class UpdateProjectParam(ProjectSchemaBase):
    """更新运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度参数"""


class DeleteProjectParam(SchemaBase):
    """删除运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度参数"""

    pks: list[int] = Field(description='运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度 ID 列表')


class GetProjectDetail(ProjectSchemaBase):
    """运营单元根（一个「号」的定位与运营单元）；双模归属 + 负责人 + 打法 + 自主度详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
