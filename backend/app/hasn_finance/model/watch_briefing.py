from datetime import datetime, date
import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_finance.model._base import HasnFinanceAppBase
from backend.common.model import id_key, UniversalText


class WatchBriefing(HasnFinanceAppBase):
    """盯盘简报（流程 D·产物·同事务登记 hasn_artifacts；流程 D 不属第一版上线门，05 §3.1.7）"""

    __tablename__ = 'watch_briefing'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='产出分身 HASN ID。为空 = 主人手工建')
    local_ref: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI')
    node_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='产出设备节点 id（溯源）')
    briefing_date: Mapped[date] = mapped_column(sa.DATE(), default_factory=date.today, comment='简报日期')
    title: Mapped[str] = mapped_column(sa.String(256), default='', comment='简报标题')
    body_md: Mapped[str] = mapped_column(UniversalText, default='', comment='简报正文（markdown）')
    covered_symbols_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='覆盖了哪些标的（按标的反查简报）')
    trigger: Mapped[str] = mapped_column(sa.String(16), default='', comment='触发 (scheduled:定时:blue/manual:手动:default)')
    revision: Mapped[int] = mapped_column(sa.BIGINT(), default=1, comment='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    usage_json: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本')
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment='状态 (active:正常:green/deleted:已删:red)')
