from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnDocSpaceSubscriptionsSchemaBase(SchemaBase):
    """社区文集订阅关系基础模型"""

    subscription_id: str = Field(description='订阅关系权威 UUID')
    space_id: str = Field(description='文集权威 space_id')
    subscriber_hasn_id: str = Field(description='订阅者 hasn_id')


class CreateHasnDocSpaceSubscriptionsParam(HasnDocSpaceSubscriptionsSchemaBase):
    """创建社区文集订阅关系参数"""


class UpdateHasnDocSpaceSubscriptionsParam(HasnDocSpaceSubscriptionsSchemaBase):
    """更新社区文集订阅关系参数"""


class DeleteHasnDocSpaceSubscriptionsParam(SchemaBase):
    """删除社区文集订阅关系参数"""

    pks: list[int] = Field(description='社区文集订阅关系 ID 列表')


class GetHasnDocSpaceSubscriptionsDetail(HasnDocSpaceSubscriptionsSchemaBase):
    """社区文集订阅关系详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
