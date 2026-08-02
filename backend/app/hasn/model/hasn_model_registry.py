from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, TimeZone, UniversalText, id_key


class HasnModelRegistry(Base):
    """模型注册表（new-api 供事实、云端补语义、一处维护全平台下发）"""

    __tablename__ = 'hasn_model_registry'

    id: Mapped[id_key] = mapped_column(init=False)
    model_name: Mapped[str] = mapped_column(sa.String(128), default='', comment='网关上的模型名（同步键，全局唯一）')
    capability: Mapped[str] = mapped_column(sa.String(32), default='unclassified', comment='能力类别 (chat:对话:blue/vision:视觉理解:blue/image_generate:文生图:green/image_edit:图像编辑:green/tts:语音合成:purple/stt:语音识别:purple/video:视频生成:orange/embedding:向量化:gray/rerank:重排:gray/unclassified:待标注:red)')
    inputs: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='输入要求表，每种输入取 required/optional/unsupported，省略即 unsupported；text 恒为必需不写')
    dialect: Mapped[str | None] = mapped_column(sa.String(32), default=None, comment='入参方言 (openai:OpenAI 兼容:blue/ali:阿里通义万相:orange)')
    quality: Mapped[str | None] = mapped_column(sa.String(16), default=None, comment='质量档 (draft:草稿:gray/standard:标准:blue/high:高质量:green)')
    scenario: Mapped[str | None] = mapped_column(UniversalText, default=None, comment='适用场景一句话（给分身选型看）')
    agent_visible: Mapped[bool] = mapped_column(sa.BOOLEAN(), default=False, comment='是否对分身暴露（新同步进来的默认关闭，标注后再放开）')
    sort_order: Mapped[int] = mapped_column(sa.INTEGER(), default=0, comment='同能力内的推荐顺序（failover 优先级，小的在前）')
    vendor_name: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='供应商显示名（来自 new-api，如阿里巴巴/DeepSeek）')
    relative_cost: Mapped[Decimal | None] = mapped_column(sa.NUMERIC(12, 4), default=None, comment='new-api model_ratio 快照，仅内部/Admin 可见，用于算档位与运维核对，绝不下发')
    cost_extra: Mapped[dict] = mapped_column(postgresql.JSONB(), default_factory=dict, comment='new-api 其它计费参数原样留档（image_ratio/completion_ratio/quota_type 等），不下发')
    cost_tier_override: Mapped[str | None] = mapped_column(sa.String(16), default=None, comment='人工覆盖价格档位 (economy:经济:green/standard:标准:blue/premium:高价:orange)，留空即用算出来的')
    enable_groups: Mapped[list] = mapped_column(postgresql.JSONB(), default_factory=list, comment='可用分组（来自 new-api enable_groups）')
    upstream_status: Mapped[str] = mapped_column(sa.String(16), default='active', comment='网关状态 (active:网关上可用:green/missing:网关上已消失:red)')
    last_synced_time: Mapped[datetime | None] = mapped_column(TimeZone, default=None, comment='最近一次在网关上被看到的时间')
