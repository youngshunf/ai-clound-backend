from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class HasnAgentChannelMirrorsSchemaBase(SchemaBase):
    """HASN Agent 渠道脱敏摘要跨设备镜像基础模型"""
    mirror_id: str = Field(description='镜像行业务主键（ULID/UUID 文本），唯一')
    owner_id: str = Field(description='Owner hasn_id（数据隔离主键，所有查询强制过滤）')
    agent_hasn_id: str = Field(description='Agent hasn_id，varchar(40) 对齐 hasn_agents.hasn_id')
    channel: str = Field(description='渠道类型 (feishu:飞书:blue/weixin:微信:green/qq:QQ:purple/wecom:企业微信:orange/webhook:Webhook:gray)')
    origin_node_id: str = Field(description='上报来源 Node ID（哪台设备的 daemon 上报）')
    runtime_location: str = Field(description='运行位置快照 (local:本地桌面端:blue/cloud:云端:purple/remote:远端:green)')
    status: str = Field(description='渠道状态快照 (unbound:未绑:gray/bound:已绑:green/expired:过期:orange/failed:失败:red/unknown:未知:gray)')
    bound_account_display: str | None = Field(None, description='脱敏绑定账号展示：飞书=昵称[@domain]/微信=昵称或****后4位/QQ=昵称或****后4位；禁原始 open_id/user_id/user_openid')
    metadata_json: dict = Field(description='脱敏元数据；禁 SECRET_KEYS/_secret/_token，写库前过 _safe_json')
    last_error: str | None = Field(None, description='最近错误摘要（可空）')


class CreateHasnAgentChannelMirrorsParam(HasnAgentChannelMirrorsSchemaBase):
    """创建HASN Agent 渠道脱敏摘要跨设备镜像参数"""


class UpdateHasnAgentChannelMirrorsParam(HasnAgentChannelMirrorsSchemaBase):
    """更新HASN Agent 渠道脱敏摘要跨设备镜像参数"""


class DeleteHasnAgentChannelMirrorsParam(SchemaBase):
    """删除HASN Agent 渠道脱敏摘要跨设备镜像参数"""

    pks: list[int] = Field(description='HASN Agent 渠道脱敏摘要跨设备镜像 ID 列表')


class GetHasnAgentChannelMirrorsDetail(HasnAgentChannelMirrorsSchemaBase):
    """HASN Agent 渠道脱敏摘要跨设备镜像详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None


class UpsertChannelMirrorRequest(SchemaBase):
    """daemon 上报脱敏渠道摘要请求体（owner_id 不取自 body，由 JWT 解析覆盖）。

    设计 §6.3：owner_id 由 JWT 解析强制覆盖，不信任 body 的 owner_id（故此处无 owner_id 字段）。
    """

    agent_hasn_id: str = Field(description='Agent hasn_id')
    channel: str = Field(description='渠道类型 feishu/weixin/qq/wecom/webhook')
    origin_node_id: str = Field(description='上报来源 Node ID（哪台设备的 daemon 上报）')
    runtime_location: str = Field('local', description='运行位置快照 local/cloud/remote')
    status: str = Field('unbound', description='渠道状态快照 unbound/bound/expired/failed/unknown')
    bound_account_display: str | None = Field(None, description='脱敏绑定账号展示（§3.3 格式）')
    metadata_json: dict = Field(default_factory=dict, description='脱敏元数据（写库前再过 safe_json 兜底）')
    last_error: str | None = Field(None, description='最近错误摘要（可空）')
