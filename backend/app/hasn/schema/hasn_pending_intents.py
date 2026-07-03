from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnPendingIntentsSchemaBase(SchemaBase):
    """HASN 第三方渠道反向 onboarding pending intent 基础模型"""
    intent_id: str = Field(description='Pending intent 唯一 ID (pi_{uuid})')
    channel_type: str = Field(description='渠道类型 (wechat:微信:green/feishu:飞书:blue/qq:QQ:cyan/webhook:Webhook:purple)')
    external_user_id: str = Field(description='第三方渠道用户 ID')
    owner_id: str | None = Field(None, description='已解析 Owner hasn_id（可空，onboarding 后回填）')
    agent_hasn_id: str | None = Field(None, description='目标 Agent hasn_id（可空）')
    conversation_hint: str | None = Field(None, description='渠道会话提示 ID')
    intent_type: str = Field(description='意图类型 (onboarding:反向登录:blue/message:待投递消息:green/channel_bind:渠道绑定:purple)')
    payload: dict = Field(description='待处理载荷摘要')
    status: str = Field(description='状态 (pending:待处理:blue/consumed:已消费:green/expired:已过期:gray/revoked:已撤销:red)')
    expires_at: datetime = Field(description='过期时间（默认 TTL 24h，由业务层设置）')
    consumed_at: datetime | None = Field(None, description='消费时间')


class CreateHasnPendingIntentsParam(HasnPendingIntentsSchemaBase):
    """创建HASN 第三方渠道反向 onboarding pending intent 参数"""


class UpdateHasnPendingIntentsParam(HasnPendingIntentsSchemaBase):
    """更新HASN 第三方渠道反向 onboarding pending intent 参数"""


class DeleteHasnPendingIntentsParam(SchemaBase):
    """删除HASN 第三方渠道反向 onboarding pending intent 参数"""

    pks: list[int] = Field(description='HASN 第三方渠道反向 onboarding pending intent  ID 列表')


class GetHasnPendingIntentsDetail(HasnPendingIntentsSchemaBase):
    """HASN 第三方渠道反向 onboarding pending intent 详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
