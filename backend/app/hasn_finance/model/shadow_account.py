from datetime import datetime, date
from uuid import UUID

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_finance.model._base import HasnFinanceAppBase
from backend.common.model import id_key, TimeZone
from backend.utils.timezone import timezone


class ShadowAccount(HasnFinanceAppBase):
    """影子账户（流程 C·产物+容器·隐私最敏感·可挂平台项目，05 §3.1.5）"""

    __tablename__ = 'shadow_account'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='产出分身 HASN ID。为空 = 主人手工建')
    local_ref: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI')
    node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='产出设备节点 id（溯源）')
    broker: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='券商')
    account_alias: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='主人给的别名（「我的打新账户」）。★隐私红线：绝不存真实账号')
    stmt_period_start: Mapped[date | None] = mapped_column(sa.DATE(), default=None, comment='对账单覆盖区间起')
    stmt_period_end: Mapped[date | None] = mapped_column(sa.DATE(), default=None, comment='对账单覆盖区间止')
    profile_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='交易画像（持仓周期/交易频率/胜率/盈亏比/偏好标的）。★高度敏感：仅主人确认同步清单后才上推')
    behaviors_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='行为诊断（处置效应/过度交易/追涨/锚定）。★高度敏感：同上')
    source_file_name: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='脱敏显示名；basename 后仍须清除账号/用户名，无法可靠脱敏就置 NULL——不是原始文件名备份')
    source_hash: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='已上传分享快照的 sha256，必须与 source_asset_uri 对应；P1 恒为 NULL')
    source_asset_uri: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='未来显式分享原件后才有 hasn://asset/{id}；P1 恒为 NULL')
    source_synced_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='原件分享快照上传时刻；P1 恒为 NULL')
    version: Mapped[int] = mapped_column(sa.INTEGER(), default=1, comment='版本号：这季度 vs 上季度')
    superseded_by: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='被哪个新版本取代（自引用；复合 FK 保证版本链不跨主人）')
    platform_project_id: Mapped[str | UUID | None] = mapped_column(sa.UUID(), default=None, comment='挂靠的平台项目 id（doc38 层2 容器级挂靠，可空=不挂；项目不是权限边界/挂载点/容器接管，只是视角）')
    revision: Mapped[int] = mapped_column(sa.BIGINT(), default=1, comment='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    usage_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本')
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment='状态 (active:正常:green/deleted:已删:red)')
