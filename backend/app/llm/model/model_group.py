"""模型组表"""

import sqlalchemy as sa

from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class ModelGroup(Base):
    """模型组表"""

    __tablename__ = 'llm_model_group'

    id: Mapped[id_key] = mapped_column(init=False)
    name: Mapped[str] = mapped_column(sa.String(64), unique=True, index=True, comment='组名称')
    model_type: Mapped[str] = mapped_column(sa.String(32), index=True, comment='模型类型')
    model_ids: Mapped[list] = mapped_column(sa.JSON, default=list, comment='模型 ID 列表(按优先级排序)')
    fallback_enabled: Mapped[bool] = mapped_column(default=True, comment='启用故障转移')
    retry_count: Mapped[int] = mapped_column(default=3, comment='重试次数')
    timeout_seconds: Mapped[int] = mapped_column(default=60, comment='超时秒数')
    enabled: Mapped[bool] = mapped_column(default=True, index=True, comment='是否启用')
    description: Mapped[str | None] = mapped_column(sa.String(256), default=None, comment='描述')
