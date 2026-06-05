from datetime import datetime
from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnWorkbenchBriefingFeedbackSchemaBase(SchemaBase):
    """HASN 工作台简报反馈（云端权威）基础模型"""
    owner_hasn_id: str = Field(description='主人 HASN ID（owner 隔离键）')
    period: str = Field(description='所属简报周期 YYYY-MM-DD')
    item_id: str = Field(description='被标记的关注项 item_id')
    action: str = Field(description='反馈动作 (dismiss:已知道:gray/done:已处理:green)')
    source_ref: str | None = Field(None, description='关注项溯源 source.ref（去重用）')
    note: str | None = Field(None, description='备注（可空）')


class CreateHasnWorkbenchBriefingFeedbackParam(HasnWorkbenchBriefingFeedbackSchemaBase):
    """创建HASN 工作台简报反馈（云端权威）参数"""


class UpdateHasnWorkbenchBriefingFeedbackParam(HasnWorkbenchBriefingFeedbackSchemaBase):
    """更新HASN 工作台简报反馈（云端权威）参数"""


class DeleteHasnWorkbenchBriefingFeedbackParam(SchemaBase):
    """删除HASN 工作台简报反馈（云端权威）参数"""

    pks: list[int] = Field(description='HASN 工作台简报反馈（云端权威） ID 列表')


class GetHasnWorkbenchBriefingFeedbackDetail(HasnWorkbenchBriefingFeedbackSchemaBase):
    """HASN 工作台简报反馈（云端权威）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
