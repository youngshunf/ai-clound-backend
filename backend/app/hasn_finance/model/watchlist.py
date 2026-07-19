from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_finance.model._base import HasnFinanceAppBase
from backend.common.model import id_key, UniversalText


class Watchlist(HasnFinanceAppBase):
    """自选股（人工维护·非产物·不登记 hasn_artifacts，05 §3.1.1）"""

    __tablename__ = 'watchlist'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_id: Mapped[str] = mapped_column(sa.String(40), default='', comment='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    symbol: Mapped[str] = mapped_column(sa.String(16), default='', comment='标的代码（600519 / 00700 / AAPL）')
    market: Mapped[str] = mapped_column(sa.String(8), default='', comment='市场 (cn:A股:red/hk:港股:orange/us:美股:blue)')
    display_name: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='名称快照（贵州茅台）。快照非权威——实时名走行情服务')
    note: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='主人自己的备注')
    sort_order: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='排序序号（主人手工拖拽次序）')
    revision: Mapped[int] = mapped_column(sa.BIGINT(), default=1, comment='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    status: Mapped[str] = mapped_column(sa.String(16), default='active', comment='状态 (active:正常:green/deleted:已删:red)')
