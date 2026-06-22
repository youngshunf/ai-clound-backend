"""量化研究用户端（owner）API 请求模型（设计 23 §6/§7）。

owner 面给主人在 WebUI 操作（写策略/提交回测/读绩效），经 daemon `/api/v1/quant/*` 薄代理调用（铁律）。
写类参数走 Pydantic 校验（fail-fast），与 Agent 工具面（gateway_internal handler 收裸 dict）互补。
身份恒取自 Owner JWT（request.user.id → owner_hasn_id），**绝不从 body 读身份**。

本期主线为回测研究（不动钱）；实盘线（deploy_live/submit_order，P6+ 真钱强闸）不在 owner 写面暴露。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from backend.common.schema import SchemaBase


class SaveStrategyParam(SchemaBase):
    """新建/更新策略（传 strategy_id 即更新，version 自增）。"""

    strategy_id: int | None = Field(default=None, description='传则更新该策略；不传为新建')
    name: str | None = Field(default=None, max_length=120, description='策略名（新建必填）')
    description: str | None = Field(default=None, max_length=2000)
    code: str = Field(default='', description='Python Strategy 子类源码（沙箱执行；与 builtin_strategy 二选一）')
    strategy_class: str = Field(default='', max_length=120, description='入口类名（用 code 时必填）')
    builtin_strategy: str | None = Field(
        default=None, max_length=80, description='内置策略键（如 ema_cross_long_only；设了则用内置不读 code）'
    )
    params: dict[str, Any] | None = Field(default=None, description='策略参数（fast_ema_period/slow_ema_period/trade_size…）')
    instrument_ids: list[str] | None = Field(default=None, description='标的列表（["ETHUSDT.BINANCE"]；回测可空）')
    venue: str | None = Field(default=None, max_length=40, description='目标场所（回测可空）')


class SubmitBacktestParam(SchemaBase):
    """提交一次回测（job 式：立即返回 queued/running，再轮询 get_backtest）。

    策略来源：strategy_id（载已存策略）或内联 builtin_strategy / code+strategy_class；
    都不给则回落内置 EMA 演示（自检/演示）。回测只花算力、**不动钱**。
    """

    strategy_id: int | None = Field(default=None, description='载已存策略（owner 隔离）')
    builtin_strategy: str | None = Field(default=None, max_length=80, description='内联内置策略键')
    code: str | None = Field(default=None, description='内联自定义策略源码')
    strategy_class: str | None = Field(default=None, max_length=120, description='内联策略入口类名')
    dataset: str = Field(default='synthetic-oscillator-eth', max_length=120, description='回测数据集键（本期合成确定性数据）')
    params: dict[str, Any] | None = Field(default=None, description='覆盖参数')
    starting_balance: float = Field(default=1_000_000.0, gt=0, description='起始资金（回测计价，非真钱）')
    trade_size: float | None = Field(default=None, gt=0, description='单笔下单量')
    fast_ema_period: int | None = Field(default=None, ge=1, le=500, description='快线周期（EMA 内置策略）')
    slow_ema_period: int | None = Field(default=None, ge=1, le=1000, description='慢线周期（EMA 内置策略）')
