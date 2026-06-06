from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnAgentChannelMirrors(Base):
    """HASN Agent 渠道脱敏摘要跨设备镜像表"""

    __tablename__ = 'hasn_agent_channel_mirrors'

    id: Mapped[id_key] = mapped_column(init=False)
    mirror_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='镜像行业务主键（ULID/UUID 文本），唯一')
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Owner hasn_id（数据隔离主键，所有查询强制过滤）')
    agent_hasn_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='Agent hasn_id，varchar(40) 对齐 hasn_agents.hasn_id')
    channel: Mapped[str] = mapped_column(sa.String(30), default='', comment='渠道类型 (feishu:飞书:blue/weixin:微信:green/qq:QQ:purple/wecom:企业微信:orange/webhook:Webhook:gray)')
    origin_node_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='上报来源 Node ID（哪台设备的 daemon 上报）')
    runtime_location: Mapped[str] = mapped_column(sa.String(50), default='', comment='运行位置快照 (local:本地桌面端:blue/cloud:云端:purple/remote:远端:green)')
    status: Mapped[str] = mapped_column(sa.String(30), default='', comment='渠道状态快照 (unbound:未绑:gray/bound:已绑:green/expired:过期:orange/failed:失败:red/unknown:未知:gray)')
    bound_account_display: Mapped[str | None] = mapped_column(sa.String(128), default=None, comment='脱敏绑定账号展示：飞书=昵称[@domain]/微信=昵称或****后4位/QQ=昵称或****后4位；禁原始 open_id/user_id/user_openid')
    metadata_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='脱敏元数据；禁 SECRET_KEYS/_secret/_token，写库前过 _safe_json')
    last_error: Mapped[str | None] = mapped_column(sa.String(500), default=None, comment='最近错误摘要（可空）')
