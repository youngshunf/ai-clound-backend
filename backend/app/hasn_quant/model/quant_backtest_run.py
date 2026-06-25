from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.hasn_quant.model._base import HasnQuantAppBase
from backend.common.model import TimeZone, UniversalText, id_key


class QuantBacktestRun(HasnQuantAppBase):
    """回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）"""

    __tablename__ = 'quant_backtest_run'

    id: Mapped[id_key] = mapped_column(init=False)
    strategy_id: Mapped[int | None] = mapped_column(
        sa.BIGINT(), default=None, comment='关联策略 id（可空：内联/即席回测无已存策略；策略删除则置空保留绩效）'
    )
    owner_hasn_id: Mapped[str] = mapped_column(sa.String(64), default='', comment='归属主人 hasn_id（行级隔离）')
    agent_hasn_id: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment=None)
    params: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='本次回测覆盖参数（快照，不回指策略当前值）')
    dataset: Mapped[str | None] = mapped_column(sa.String(60), default=None, comment='回测数据集键（synthetic-oscillator-eth…；本期合成确定性数据）')
    data_source: Mapped[str | None] = mapped_column(sa.String(40), default=None, comment='数据源（databento/tardis/catalog/synthetic…）')
    data_start: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    data_end: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (queued:排队:gray/running:运行中:blue/succeeded:成功:green/failed:失败:red)')
    metrics: Mapped[dict | None] = mapped_column(postgresql.JSONB(), default=None, comment='绩效 {sharpe,sortino,max_drawdown,total_return,win_rate,profit_factor,trades_count,fills_count…}')
    equity_curve: Mapped[list | None] = mapped_column(postgresql.JSONB(), default=None, comment='净值曲线点序列（UI 画线；大数据集落桶 equity_curve_asset_uri）')
    equity_curve_asset_uri: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='净值曲线产物（私有桶 hasn://asset/…）')
    report_asset_uri: Mapped[str | None] = mapped_column(sa.String(512), default=None, comment='完整报告产物（私有桶）')
    engine_job_id: Mapped[str | None] = mapped_column(sa.String(80), default=None, comment='引擎侧 job 标识（云端轮询用）')
    error: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='失败真实错误（透传，零 fake）')
    duration_secs: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(), default=None, comment='引擎回测耗时（秒）')
    started_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
    finished_at: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment=None)
