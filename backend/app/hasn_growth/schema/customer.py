from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class CustomerSchemaBase(SchemaBase):
    """获客客户（qualified 线索 / inbound 直建）基础模型"""
    customer_no: str = Field(description='None')
    user_id: int = Field(description='None')
    lead_contact_id: int | None = Field(None, description='None')
    source_kind: str = Field(description='来源 (outbound_crawl:采集:blue/inbound_form:留资:green/community:内容:purple/manual:手动:gray)')
    company_name: str | None = Field(None, description='None')
    contact_name: str | None = Field(None, description='None')
    email: str | None = Field(None, description='None')
    phone: str | None = Field(None, description='None')
    wechat: str | None = Field(None, description='None')
    im_refs: dict = Field(description='None')
    profile_json: dict = Field(description='AI 画像（行业/规模/痛点/预算信号/决策角色/沟通偏好）')
    intent_score: Decimal = Field(description='意向分（分身每次跟进后更新）')
    lifecycle_status: str = Field(description='生命周期 (active:跟进中:blue/engaged:有回应:cyan/opportunity:已立商机:purple/silent:沉默:gray/won:成交:green/lost:流失:red/archived:归档:gray)')
    owner_agent_id: str | None = Field(None, description='None')
    followup_task_id: str | None = Field(None, description='当前跟进任务（hasn_task.task.id 逻辑引用）')
    tags: dict = Field(description='None')
    last_activity_at: datetime | None = Field(None, description='None')
    next_followup_at: datetime | None = Field(None, description='None')
    silent_round_count: int = Field(description='None')


class CreateCustomerParam(CustomerSchemaBase):
    """创建获客客户（qualified 线索 / inbound 直建）参数"""


class UpdateCustomerParam(CustomerSchemaBase):
    """更新获客客户（qualified 线索 / inbound 直建）参数"""


class DeleteCustomerParam(SchemaBase):
    """删除获客客户（qualified 线索 / inbound 直建）参数"""

    pks: list[int] = Field(description='获客客户（qualified 线索 / inbound 直建） ID 列表')


class GetCustomerDetail(CustomerSchemaBase):
    """获客客户（qualified 线索 / inbound 直建）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
