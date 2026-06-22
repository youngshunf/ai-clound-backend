import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import id_key, UniversalText
from backend.app.hasn_quant.model._base import HasnQuantAppBase


class QuantStrategy(HasnQuantAppBase):
    """量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）"""

    __tablename__ = 'quant_strategy'

    id: Mapped[id_key] = mapped_column(init=False)
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='归属主人 hasn_id（行级隔离键）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='作者分身 hasn_id（创建带归属资源默认取凭证身份，PLANFIX-6）')
    name: Mapped[str] = mapped_column(sa.String(120), default='', comment=None)
    description: Mapped[str | None] = mapped_column(UniversalText, default=None, comment=None)
    code: Mapped[str] = mapped_column(UniversalText, default='', comment='Python Strategy 子类源码（沙箱执行，AI 生成=RCE 面）')
    strategy_class: Mapped[str] = mapped_column(sa.String(120), default='', comment='入口类名（供引擎装配；与 <class>Config 约定成对）')
    builtin_strategy: Mapped[str | None] = mapped_column(sa.String(60), default=None, comment='内置策略键（如 ema_cross_long_only；设了则用内置不读 code）')
    params: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='策略参数（fast_ema/slow_ema/trade_size…）')
    instrument_ids: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment='标的列表（["ETHUSDT.BINANCE"]）')
    venue: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='目标场所（BINANCE/IB/…；回测可空）')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (draft:草稿:gray/backtested:已回测:blue/deployed:已部署:green/archived:已归档:gray)')
    version: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='版本号（每次保存自增，保留迭代 history）')
    latest_backtest_id: Mapped[int | None] = mapped_column(sa.BIGINT(), default=None, comment='最近回测 id（冗余，列表展示最近绩效）')
