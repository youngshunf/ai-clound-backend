from datetime import datetime, date
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class BacktestReportSchemaBase(SchemaBase):
    """回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）基础模型"""
    owner_id: str = Field(description='归属主人 HASN ID（owner 隔离键，所有查询必带；owner 只取鉴权上下文，客户端传入不可信）')
    agent_hasn_id: str | None = Field(None, description='产出分身 HASN ID。为空 = 主人手工建')
    local_ref: str | None = Field(None, description='本地幂等键（daemon 侧本地行 id）。仅做实体身份去重，云端从不据它解析/暴露/进 URI')
    node_id: str | None = Field(None, description='产出设备节点 id（溯源）')
    strategy_id: int | None = Field(None, description='所属策略 id（可空=临时试跑没沉淀成策略；复合 FK 保证与本行同 owner）')
    title: str = Field(description='报告标题')
    period_start: date = Field(description='样本区间起（诚实性红线的数据层强制，必填）')
    period_end: date = Field(description='样本区间止（诚实性红线的数据层强制，必填）')
    universe_json: dict = Field(description='回测标的池')
    initial_capital: Decimal | None = Field(None, description='初始资金')
    cost_model_json: dict = Field(description='手续费/滑点/印花税假设（诚实性红线：不含成本的回测是骗人的——零成本假设下高频策略遍地圣杯，加万三手续费全军覆没。UI 必须与收益指标并列展示，不许折叠）')
    benchmark_symbol: str | None = Field(None, description='基准标的（沪深300…）。诚实性红线：没有基准的年化毫无意义——策略 20% 而同期沪深300 涨 25% 即跑输大盘')
    benchmark_return: Decimal | None = Field(None, description='同期基准收益')
    annual_return: Decimal | None = Field(None, description='年化收益（拆真列：要排序/对比）')
    sharpe: Decimal | None = Field(None, description='夏普比率（拆真列：要排序/对比）')
    max_drawdown: Decimal | None = Field(None, description='最大回撤（拆真列：要排序/对比）')
    win_rate: Decimal | None = Field(None, description='胜率（拆真列：要排序/对比）')
    trade_count: int | None = Field(None, description='成交笔数（拆真列：要排序/对比）')
    metrics_json: dict = Field(description='其余指标（索提诺/卡玛/月度分布…），不排序只展示')
    equity_curve_json: dict = Field(description='净值曲线')
    trades_json: dict = Field(description='成交明细（P1 先 JSONB：详情页只展示不查询；实测单条 > 1MB 再改落 asset，先量再决定）')
    engine_version: str = Field(description='引擎版本（可复现性，必填）')
    data_source: str = Field(description='本次实际出数的数据源（必填：A股回退链有多源，不记下来则两次结果不同时无法判断是策略变化还是数据源变化）')
    revision: int = Field(description='云端单调版本；每次有效更新/删除 +1，支撑下行合并与跨设备冲突检测')
    last_client_op_id: str | None = Field(None, description='最近成功应用的 outbox op id；只用于响应丢失后的幂等回放，不对产品层暴露')
    usage_json: dict = Field(description='本次产出的模型/token/积分用量快照；账务权威仍是既有积分账本')
    status: str = Field(description='状态 (active:正常:green/deleted:已删:red)')


class CreateBacktestReportParam(BacktestReportSchemaBase):
    """创建回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）参数"""


class UpdateBacktestReportParam(BacktestReportSchemaBase):
    """更新回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）参数"""


class DeleteBacktestReportParam(SchemaBase):
    """删除回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）参数"""

    pks: list[int] = Field(description='回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4） ID 列表')


class GetBacktestReportDetail(BacktestReportSchemaBase):
    """回测报告（流程 B·产物·同事务登记 hasn_artifacts，05 §3.1.4）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
