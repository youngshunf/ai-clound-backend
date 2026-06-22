from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnWorkbenchBriefingSchemaBase(SchemaBase):
    """HASN 工作台每日关注简报（云端权威）基础模型"""
    owner_hasn_id: str = Field(description='主人 HASN ID（owner 隔离键）')
    agent_hasn_id: str = Field(description='产出该简报的主脑 HASN ID')
    period: str = Field(description='覆盖周期 YYYY-MM-DD（主人本地日期）')
    state: str = Field(description='状态 (generating:生成中:blue/ready:就绪:green/failed:失败:red)')
    document_json: dict = Field(description='完整 BriefingDocument（JSONB）')
    generated_at: datetime = Field(description='产出时间')


class CreateHasnWorkbenchBriefingParam(HasnWorkbenchBriefingSchemaBase):
    """创建HASN 工作台每日关注简报（云端权威）参数"""


class UpdateHasnWorkbenchBriefingParam(HasnWorkbenchBriefingSchemaBase):
    """更新HASN 工作台每日关注简报（云端权威）参数"""


class DeleteHasnWorkbenchBriefingParam(SchemaBase):
    """删除HASN 工作台每日关注简报（云端权威）参数"""

    pks: list[int] = Field(description='HASN 工作台每日关注简报（云端权威） ID 列表')


class GetHasnWorkbenchBriefingDetail(HasnWorkbenchBriefingSchemaBase):
    """HASN 工作台每日关注简报（云端权威）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
