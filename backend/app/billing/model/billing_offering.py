from datetime import datetime
import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.app.billing.model._base import BillingBase
from backend.common.model import id_key


class BillingOffering(BillingBase):
    """商品目录（一切可售卖物：LLM档/积分包/应用/席位/应用内档位）"""

    __tablename__ = 'billing_offering'

    id: Mapped[id_key] = mapped_column(init=False)
    key: Mapped[str] = mapped_column(sa.String(64), default='', comment='商品业务键（全端稳定，如 app:quant / llm:tier / webapp:hosting）')
    kind: Mapped[str] = mapped_column(sa.String(16), default='', comment='商品种类 (llm_tier:LLM订阅档:blue/credit_pack:积分包:cyan/app:应用:green/seat:企业席位:purple/feature_plan:功能档位:orange)')
    feature_key: Mapped[str] = mapped_column(sa.String(64), default='', comment='付费墙特征键（付费墙通用语言，如 app:<id> / llm:tier / webapp:hosting；集中注册表 feature_registry 校验）')
    display_name: Mapped[str] = mapped_column(sa.String(128), default='', comment='显示名称')
    status: Mapped[str] = mapped_column(sa.String(16), default='', comment='状态 (active:上架:green/inactive:下架:gray)')
    source: Mapped[str] = mapped_column(sa.String(32), default='', comment='商品来源（预留分成维度，platform:平台自营）')
    sort_order: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='排序权重')
