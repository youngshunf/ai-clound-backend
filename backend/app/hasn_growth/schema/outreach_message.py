from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class OutreachMessageSchemaBase(SchemaBase):
    """获客触达消息（出/入双向，审批状态机核心表）基础模型"""

    customer_id: int = Field(description='None')
    opportunity_id: int | None = Field(None, description='None')
    user_id: int = Field(description='None')
    growth_project_id: UUID | None = Field(None, description='获客漏斗 UUID')
    growth_project_playbook_id: int | None = Field(None, description='执行时项目打法采用关系 ID')
    playbook_id: int | None = Field(None, description='执行时打法 ID')
    playbook_version: int | None = Field(None, description='执行时打法版本')
    agent_id: str | None = Field(None, description='None')
    direction: str = Field(description='方向 (outbound:出:blue/inbound:入:green)')
    channel: str = Field(
        description=(
            '渠道 (manual_assist:人工辅助:gray/wechat:微信:green/qq:QQ:blue/'
            'feishu:飞书:cyan/email:邮件:orange/hasn_dm:站内:purple)'
        )
    )
    subject: str | None = Field(None, description='None')
    content: str = Field(description='None')
    content_assets: dict = Field(description='None')
    status: str = Field(
        description=(
            '状态 (draft:草稿:gray/pending_approval:待审批:orange/approved:已批准:blue/'
            'sending:发送中:cyan/sent:已发送:green/replied:已回复:purple/rejected:已拒绝:red/'
            'failed:失败:red/blocked_optout:退订拦截:red/blocked_compliance:合规拦截:red)'
        )
    )
    intent_note: str | None = Field(None, description='给主人看的一句话：为什么现在发这条')
    approval_user_id: int | None = Field(None, description='None')
    approved_at: datetime | None = Field(None, description='None')
    reject_reason: str | None = Field(None, description='None')
    auto_approved: bool = Field(False, description='白名单放行标记（默认关闭，审计区分人批/自动）')
    task_run_id: str | None = Field(None, description='None')
    workflow_run_id: str | None = Field(None, description='None')
    sent_at: datetime | None = Field(None, description='None')
    replied_at: datetime | None = Field(None, description='None')
    error_message: str | None = Field(None, description='None')
    compliance_check: dict = Field(description='None')
    dedupe_key: str | None = Field(None, description='None')


class CreateOutreachMessageParam(OutreachMessageSchemaBase):
    """创建获客触达消息（出/入双向，审批状态机核心表）参数"""


class UpdateOutreachMessageParam(OutreachMessageSchemaBase):
    """更新获客触达消息（出/入双向，审批状态机核心表）参数"""


class DeleteOutreachMessageParam(SchemaBase):
    """删除获客触达消息（出/入双向，审批状态机核心表）参数"""

    pks: list[int] = Field(description='获客触达消息（出/入双向，审批状态机核心表） ID 列表')


class GetOutreachMessageDetail(OutreachMessageSchemaBase):
    """获客触达消息（出/入双向，审批状态机核心表）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
