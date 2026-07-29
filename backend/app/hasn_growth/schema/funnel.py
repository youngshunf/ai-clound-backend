"""获客漏斗 API 请求模型（设计 07 §5/§8/§9）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from backend.common.schema import SchemaBase

OutreachChannel = Literal['manual_assist', 'wechat', 'qq', 'feishu', 'email', 'hasn_dm']


class QualifyLeadParam(SchemaBase):
    profile: dict[str, Any] = Field(default_factory=dict, description='画像快照')
    intent_score: float | None = Field(default=None, ge=0, le=100, description='意向分')


class DismissLeadParam(SchemaBase):
    reason: str = Field(min_length=1, max_length=500, description='不合格原因')


class CreateLeadParam(SchemaBase):
    """主人在 UI 手动新建线索（owner 私有池，source_type=manual）。

    AI-native 宗旨：UI 给人操作、工具给 agent 用。主人手动建线索与分身 `collect` 采集互补，
    至少填公司名或联系人名之一（service 层校验，否则线索无意义）。
    """

    company_name: str | None = Field(default=None, max_length=255)
    contact_name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    website: str | None = Field(default=None, max_length=500)
    industry: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=2000, description='备注（落 metadata.note）')
    intent_score: float | None = Field(default=None, ge=0, le=100, description='初始匹配分（缺省 60）')


class RequestLeadsParam(SchemaBase):
    """主人「请求线索」（阶段二 2.3 用户端默认入口）：先查公共池命中即交付，缺口才后台补爬。

    向用户表达「请求线索」而非「发起采集」——采集是平台黑盒行为（doc08 §4 数据飞轮）。
    行业/地区/关键词/城市任一维度可选；数量上限护 DB。
    """

    industry: str | None = Field(default=None, max_length=100, description='行业（归一到标准 code 再查池）')
    region: str | None = Field(default=None, max_length=100, description='地区/省（ilike 模糊）')
    city: str | None = Field(default=None, max_length=100, description='城市（ilike 模糊）')
    keyword: str | None = Field(default=None, max_length=200, description='关键词（公司名/行业/联系人 ilike）')
    limit: int = Field(default=20, ge=1, le=100, description='请求线索数量 N')


class UpdateCustomerParam(SchemaBase):
    profile: dict[str, Any] | None = Field(default=None)
    intent_score: float | None = Field(default=None, ge=0, le=100)
    tags: list[str] | None = Field(default=None)
    lifecycle_status: str | None = Field(default=None, description='生命周期态')
    followup_task_id: str | None = Field(
        default=None, max_length=64, description='绑定当前跟进任务（hasn_task task_uuid，J3 即时跟进据此触发 run_now）'
    )


class ChannelSettingParam(SchemaBase):
    wechat_auto_send_confirmed: bool = Field(
        description='微信自动发送已确认（J1 高风险闸门；UI 二次确认后才置 true，关闭随时）'
    )


class LogActivityParam(SchemaBase):
    kind: str = Field(description='活动类型 note/call/meeting/...')
    content: str = Field(min_length=1, max_length=2000)
    opportunity_id: int | None = Field(default=None)


class SendOutreachParam(SchemaBase):
    customer_id: int = Field(description='客户 ID')
    channel: OutreachChannel = Field(description='渠道 manual_assist/wechat/qq/feishu/email/hasn_dm')
    content: str = Field(min_length=1, description='话术正文（不含明文联系方式）')
    subject: str | None = Field(default=None, max_length=200)
    intent_note: str | None = Field(default=None, max_length=500, description='给主人看的一句话：为什么现在发')
    content_assets: dict[str, Any] = Field(default_factory=dict)
    opportunity_id: int | None = Field(default=None)


class DraftOutreachParam(SendOutreachParam):
    idempotency_key: str = Field(
        min_length=1,
        max_length=200,
        description='调用方稳定幂等键；重试必须复用',
    )


class SubmitOutreachParam(SchemaBase):
    expected_content_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)


class ApproveOutreachParam(SchemaBase):
    edited_content: str | None = Field(default=None, description='改话术后批（留痕）')
    expected_content_version: int | None = Field(default=None, ge=1)


class VersionedApproveOutreachParam(SchemaBase):
    """项目化审批必须显式携带用户看到的内容版本。"""

    edited_content: str | None = Field(default=None, description='改话术后批（留痕）')
    expected_content_version: int = Field(ge=1)


class RejectOutreachParam(SchemaBase):
    reason: str = Field(min_length=1, max_length=500)
    expected_content_version: int | None = Field(default=None, ge=1)


class VersionedRejectOutreachParam(SchemaBase):
    """项目化拒绝必须显式携带用户看到的内容版本。"""

    reason: str = Field(min_length=1, max_length=500)
    expected_content_version: int = Field(ge=1)


class EditOutreachParam(SchemaBase):
    expected_content_version: int = Field(ge=1)
    content: str = Field(min_length=1)
    subject: str | None = Field(default=None, max_length=200)
    channel: OutreachChannel | None = None
    content_assets: dict[str, Any] | None = None


class ManualAttestOutreachParam(SchemaBase):
    expected_content_version: int = Field(ge=1)
    channel_actual: OutreachChannel
    idempotency_key: str = Field(min_length=1, max_length=200)
    proof: dict[str, Any] = Field(
        default_factory=dict,
        description='人工操作证明元数据，不得含联系方式明文',
    )


class MarkSentParam(SchemaBase):
    channel_actual: OutreachChannel | None = Field(default=None, description='manual_assist 实际发送渠道')


class CreateOpportunityParam(SchemaBase):
    customer_id: int
    name: str = Field(min_length=1, max_length=200)
    amount: float | None = Field(default=None, ge=0)
    currency: str = Field(default='CNY', pattern=r'^[A-Z]{3}$')
    stage: Literal['contacted', 'replied', 'proposal', 'negotiation'] = 'contacted'
    probability: float | None = Field(default=None, ge=0, le=1)
    idempotency_key: str = Field(min_length=1, max_length=150)


class UpdateStageParam(SchemaBase):
    stage: Literal['contacted', 'replied', 'proposal', 'negotiation']
    note: str = Field(min_length=1, max_length=500)
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=150)


class CloseDealParam(SchemaBase):
    result: Literal['won', 'lost']
    amount: float | None = Field(default=None, gt=0)
    currency: str | None = Field(default=None, pattern=r'^[A-Z]{3}$')
    close_note: str | None = Field(default=None, max_length=2000)
    lost_reason: str | None = Field(default=None, min_length=1, max_length=500)
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=150)

    @model_validator(mode='after')
    def validate_close_facts(self) -> CloseDealParam:
        if self.result == 'won' and (self.amount is None or self.currency is None):
            raise ValueError('成交必须填写金额和币种')
        if self.result == 'lost' and not (self.lost_reason or '').strip():
            raise ValueError('流失必须填写结构化原因')
        return self


class FormSubmitParam(SchemaBase):
    """落地页表单回流（open，无鉴权）。"""

    company_name: str | None = Field(default=None, max_length=255)
    contact_name: str | None = Field(default=None, max_length=100)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    wechat: str | None = Field(default=None, max_length=100)
    message: str | None = Field(default=None, max_length=2000)
    extra: dict[str, Any] = Field(default_factory=dict)
    privacy_notice_version: str = Field(
        min_length=1,
        max_length=64,
        pattern=r'^[A-Za-z0-9][A-Za-z0-9._-]*$',
    )
    consent_purpose: Literal['sales_followup']
    consent_granted: Literal[True]
    # 蜜罐字段：人类不可见，被填充即判 spam（反滥用，§8.4）。
    website_url: str | None = Field(default=None, description='蜜罐字段，正常用户应留空')


class OptoutParam(SchemaBase):
    channel: str = Field(default='all', max_length=24)
    address: str = Field(min_length=1, description='退订的联系方式（仅算 hash，不落明文）')
    reason: str | None = Field(default=None, max_length=500)


class AssignOwnerParam(SchemaBase):
    """企业经理分配/转移客户负责人（GE4，仅 enterprise 经理；assignee 为人或分身 hasn_id）。"""

    assignee: str = Field(min_length=1, max_length=64, description='新负责人 hasn_id（人或分身）')
