from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnContactsSchemaBase(SchemaBase):
    """HASN 联系人关系基础模型"""
    owner_id: str = Field(description='关系拥有者 hasn_id')
    peer_id: str = Field(description='对方 hasn_id')
    peer_owner_id: str | None = Field(None, description='对方归属人 hasn_id (peer 自己的 owner)')
    peer_type: str = Field(description='对方类型 (human:人类:blue/agent:代理:green)')
    relation_type: str = Field(description='关系类型 (social:社交:blue/commerce:商业:orange/service:履约:green/professional:专业:purple/platform:平台:cyan)')
    trust_level: int = Field(description='信任等级 (0:已拉黑:red/1:陌生人:gray/2:普通联系人:blue/3:朋友:green/4:高信任:orange/5:所有者:purple)')
    scope: dict | None = Field(None, description='关系作用域 (JSONB)')
    custom_permissions: dict = Field(description='自定义权限覆盖 (JSONB)')
    nickname: str | None = Field(None, description='备注名')
    tags: list[str] | None = Field(None, description='分组标签')
    subscription: bool = Field(description='是否订阅推送')
    channel_source: str | None = Field(None, description='来源渠道类型 (wechat/feishu/qq/webhook/manual/system)')
    status: str = Field(description='状态 (pending:待处理:blue/connected:已连接:green/blocked:已拉黑:red/archived:已归档:gray)')
    request_message: str | None = Field(None, description='好友请求附言')
    auto_expire: datetime | None = Field(None, description='自动过期时间')
    connected_at: datetime | None = Field(None, description='建立连接时间')
    last_interaction_at: datetime | None = Field(None, description='最后互动时间')
    interaction_count: int = Field(description='互动次数')


class CreateHasnContactsParam(HasnContactsSchemaBase):
    """创建HASN 联系人关系参数"""


class UpdateHasnContactsParam(HasnContactsSchemaBase):
    """更新HASN 联系人关系参数"""


class DeleteHasnContactsParam(SchemaBase):
    """删除HASN 联系人关系参数"""

    pks: list[int] = Field(description='HASN 联系人关系 ID 列表')


class GetHasnContactsDetail(HasnContactsSchemaBase):
    """HASN 联系人关系详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
