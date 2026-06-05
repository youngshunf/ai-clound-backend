from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnOwnerWorkbenchPrefSchemaBase(SchemaBase):
    """HASN 主人工作台偏好（主脑指针 + 每日简报偏好）基础模型"""

    owner_hasn_id: str = Field(description='主人 hasn_id（每人一行，唯一）')
    primary_agent_id: str | None = Field(None, description='主脑分身 hasn_id（空=回落首个分身）')
    briefing_enabled: bool = Field(description='每日简报开关')
    briefing_time: str = Field(description='简报生成时刻（本地时区 HH:MM）')
    briefing_sources: list[str] = Field(description='简报数据源开关（数组：task/social/app/plan）')


class CreateHasnOwnerWorkbenchPrefParam(HasnOwnerWorkbenchPrefSchemaBase):
    """创建HASN 主人工作台偏好参数"""


class UpdateHasnOwnerWorkbenchPrefParam(HasnOwnerWorkbenchPrefSchemaBase):
    """更新HASN 主人工作台偏好参数"""


class GetHasnOwnerWorkbenchPrefDetail(HasnOwnerWorkbenchPrefSchemaBase):
    """HASN 主人工作台偏好详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None


class PutWorkbenchPrefParam(SchemaBase):
    """工作台偏好部分更新参数（PUT，字段均可选；省略=不改）"""

    primary_agent_id: str | None = Field(None, description='主脑分身 hasn_id（设为某分身；归属由服务端校验）')
    briefing_enabled: bool | None = Field(None, description='每日简报开关')
    briefing_time: str | None = Field(None, description='简报生成时刻（本地时区 HH:MM，24h）')
    briefing_sources: list[str] | None = Field(None, description='简报数据源开关（task/social/app/plan）')


class WorkbenchPrefResponse(SchemaBase):
    """工作台偏好对外返回（含解析后的有效主脑：primary_agent_id 为空时回落首个分身）"""

    owner_hasn_id: str = Field(description='主人 hasn_id')
    primary_agent_id: str | None = Field(None, description='主脑分身 hasn_id（已解析有效值，含回落）')
    primary_agent_explicit: bool = Field(description='主脑是否为主人显式设置（false=回落首个分身）')
    briefing_enabled: bool = Field(description='每日简报开关')
    briefing_time: str = Field(description='简报生成时刻（本地时区 HH:MM）')
    briefing_sources: list[str] = Field(description='简报数据源开关')
