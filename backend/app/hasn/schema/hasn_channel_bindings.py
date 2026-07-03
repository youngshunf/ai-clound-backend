from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnChannelBindingsSchemaBase(SchemaBase):
    """HASN Channel Binding 基础模型"""
    binding_id: str = Field(description='Channel Binding 唯一 ID (cb_{uuid})')
    owner_id: str = Field(description='Owner hasn_id')
    agent_hasn_id: str | None = Field(None, description='绑定 Agent hasn_id（可空表示 Owner 级绑定）')
    channel_type: str = Field(description='渠道类型 (wechat:微信:green/feishu:飞书:blue/qq:QQ:cyan/webhook:Webhook:purple)')
    external_user_id: str = Field(description='第三方渠道用户 ID')
    external_chat_id: str | None = Field(None, description='第三方渠道会话/群 ID（可空）')
    display_name: str | None = Field(None, description='渠道侧展示名')
    binding_scope: str = Field(description='绑定范围 (owner:Owner:blue/agent:Agent:green/group:群聊:purple)')
    status: str = Field(description='状态 (active:生效中:green/revoked:已吊销:red/deleted:已删除:gray)')
    policy_json: dict = Field(description='渠道策略摘要')
    last_inbound_at: datetime | None = Field(None, description='最近入站时间')
    last_outbound_at: datetime | None = Field(None, description='最近出站时间')
    revoked_at: datetime | None = Field(None, description='吊销时间')


class CreateHasnChannelBindingsParam(HasnChannelBindingsSchemaBase):
    """创建HASN Channel Binding 参数"""


class UpdateHasnChannelBindingsParam(HasnChannelBindingsSchemaBase):
    """更新HASN Channel Binding 参数"""


class DeleteHasnChannelBindingsParam(SchemaBase):
    """删除HASN Channel Binding 参数"""

    pks: list[int] = Field(description='HASN Channel Binding  ID 列表')


class GetHasnChannelBindingsDetail(HasnChannelBindingsSchemaBase):
    """HASN Channel Binding 详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
