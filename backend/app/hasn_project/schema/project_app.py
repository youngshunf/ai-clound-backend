"""平台项目 Owner API 的严格写入契约（doc38 C2）。"""

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
