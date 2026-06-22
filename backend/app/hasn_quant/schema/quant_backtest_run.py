from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class QuantBacktestRunSchemaBase(SchemaBase):
    """回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）基础模型"""
    strategy_id: int = Field(description='None')
    owner_hasn_id: str = Field(description='归属主人 hasn_id（行级隔离）')
    agent_hasn_id: str | None = Field(None, description='None')
    params: dict = Field(description='本次回测覆盖参数（快照，不回指策略当前值）')
    dataset: str | None = Field(None, description='回测数据集键（synthetic-oscillator-eth…；本期合成确定性数据）')
    data_source: str | None = Field(None, description='数据源（databento/tardis/catalog/synthetic…）')
    data_start: datetime | None = Field(None, description='None')
    data_end: datetime | None = Field(None, description='None')
    status: str = Field(description='状态 (queued:排队:gray/running:运行中:blue/succeeded:成功:green/failed:失败:red)')
    metrics: dict | None = Field(None, description='绩效 {sharpe,sortino,max_drawdown,total_return,win_rate,profit_factor,trades_count,fills_count…}')
    equity_curve: dict | None = Field(None, description='净值曲线点序列（UI 画线；大数据集落桶 equity_curve_asset_uri）')
    equity_curve_asset_uri: str | None = Field(None, description='净值曲线产物（私有桶 hasn://asset/…）')
    report_asset_uri: str | None = Field(None, description='完整报告产物（私有桶）')
    engine_job_id: str | None = Field(None, description='引擎侧 job 标识（云端轮询用）')
    error: str | None = Field(None, description='失败真实错误（透传，零 fake）')
    duration_secs: Decimal | None = Field(None, description='引擎回测耗时（秒）')
    started_at: datetime | None = Field(None, description='None')
    finished_at: datetime | None = Field(None, description='None')


class CreateQuantBacktestRunParam(QuantBacktestRunSchemaBase):
    """创建回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）参数"""


class UpdateQuantBacktestRunParam(QuantBacktestRunSchemaBase):
    """更新回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）参数"""


class DeleteQuantBacktestRunParam(SchemaBase):
    """删除回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）参数"""

    pks: list[int] = Field(description='回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve） ID 列表')


class GetQuantBacktestRunDetail(QuantBacktestRunSchemaBase):
    """回测任务 + 绩效（job 式：提交→引擎跑→落 metrics/equity_curve）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
