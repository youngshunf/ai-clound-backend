from datetime import datetime
from decimal import Decimal

from pydantic import ConfigDict, Field

from backend.common.schema import SchemaBase


class BillingPlanSchemaBase(SchemaBase):
    """商品档位（价格+配额快照+试用/宽限策略）基础模型"""
    offering_key: str = Field(description='所属 offering 业务键（指向 billing_offering.key）')
    plan_key: str = Field(description='档位键（如 monthly/yearly/standard/once）')
    price_amount: Decimal = Field(description='价格（price_unit 单位，如元；改价只影响新购续费）')
    price_unit: str = Field(description='计价单位 (cny:人民币元:blue/credits:积分:cyan)')
    cycle: str = Field(description='计费周期 (once:一次买断:gray/month:月:blue/year:年:green)')
    quota_json: dict = Field(description='配额包快照（站点数/内存/卷/席位数/max_agents…；购买时固化进权益行）')
    trial_json: dict = Field(description='试用策略（enabled/days/times）')
    grace_json: dict = Field(description='宽限策略（remind_days/grace_days，到期提醒节奏+宽限天数）')
    status: str = Field(description='状态 (active:上架:green/inactive:下架:gray)')
    sort_order: int = Field(description='排序权重')


class CreateBillingPlanParam(BillingPlanSchemaBase):
    """创建商品档位（价格+配额快照+试用/宽限策略）参数"""


class UpdateBillingPlanParam(BillingPlanSchemaBase):
    """更新商品档位（价格+配额快照+试用/宽限策略）参数"""


class DeleteBillingPlanParam(SchemaBase):
    """删除商品档位（价格+配额快照+试用/宽限策略）参数"""

    pks: list[int] = Field(description='商品档位（价格+配额快照+试用/宽限策略） ID 列表')


class GetBillingPlanDetail(BillingPlanSchemaBase):
    """商品档位（价格+配额快照+试用/宽限策略）详情"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    created_time: datetime
    updated_time: datetime | None = None
