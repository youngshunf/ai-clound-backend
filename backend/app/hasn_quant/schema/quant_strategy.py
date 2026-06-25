from datetime import datetime

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class QuantStrategySchemaBase(SchemaBase):
    """量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）基础模型"""
    owner_hasn_id: str = Field(description='归属主人 hasn_id（行级隔离键）')
    agent_hasn_id: str | None = Field(None, description='作者分身 hasn_id（创建带归属资源默认取凭证身份，PLANFIX-6）')
    name: str = Field(description='None')
    description: str | None = Field(None, description='None')
    code: str = Field(description='Python Strategy 子类源码（沙箱执行，AI 生成=RCE 面）')
    strategy_class: str = Field(description='入口类名（供引擎装配；与 <class>Config 约定成对）')
    builtin_strategy: str | None = Field(None, description='内置策略键（如 ema_cross_long_only；设了则用内置不读 code）')
    params: dict = Field(description='策略参数（fast_ema/slow_ema/trade_size…）')
    instrument_ids: dict = Field(description='标的列表（["ETHUSDT.BINANCE"]）')
    venue: str | None = Field(None, description='目标场所（BINANCE/IB/…；回测可空）')
    status: str = Field(description='状态 (draft:草稿:gray/backtested:已回测:blue/deployed:已部署:green/archived:已归档:gray)')
    version: int = Field(description='版本号（每次保存自增，保留迭代 history）')
    latest_backtest_id: int | None = Field(None, description='最近回测 id（冗余，列表展示最近绩效）')


class CreateQuantStrategyParam(QuantStrategySchemaBase):
    """创建量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）参数"""


class UpdateQuantStrategyParam(QuantStrategySchemaBase):
    """更新量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）参数"""


class DeleteQuantStrategyParam(SchemaBase):
    """删除量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）参数"""

    pks: list[int] = Field(description='量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数） ID 列表')


class GetQuantStrategyDetail(QuantStrategySchemaBase):
    """量化策略定义（分身 AI 生成/迭代的 nautilus Strategy 子类源码 + 参数）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
