"""平台默认配置模型（云端权威，单行下发）。

一行权威（config_key='global'）承载：
  - node.media：New API 网关媒体模型默认（image/tts/stt failover 顺序）
  - agent_runtime.models：平台默认 agent 运行时四槽模型（main/fast/vision/delegation）

运营 Admin 改一处，全平台桌面端 daemon + runtime 经 revision 比对自动拉取应用。
形态属"配置/元数据单行"（非实体 CRUD），与 hasn_app_catalog 同为 public-schema 配置表，
故沿用其 ``Base`` 手写约定（id + created_time/updated_time 由 Base 提供），
不走 4-scope fba 代码生成；Admin GET/PUT + 节点 GET 由 service/API 自定义。
表结构以 ``backend/sql/hasn/hasn_platform_default_config.sql`` 为准。
"""

import sqlalchemy as sa

from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from backend.common.model import Base, id_key


class HasnPlatformDefaultConfig(Base):
    """平台默认配置（云端权威，单行下发）。"""

    __tablename__ = 'hasn_platform_default_config'

    id: Mapped[id_key] = mapped_column(init=False)
    config_key: Mapped[str] = mapped_column(
        sa.String(32), default='global', unique=True, comment='配置键（单行权威，恒 global）'
    )
    config_json: Mapped[dict] = mapped_column(
        postgresql.JSONB(), default_factory=dict, comment='平台默认配置 JSON（New API 媒体模型 + agent runtime）'
    )
    revision: Mapped[str] = mapped_column(
        sa.String(16), default='', comment='配置内容指纹 sha256(canonical_json)[:16]，daemon 比对重拉'
    )
    updated_by: Mapped[str | None] = mapped_column(sa.String(64), default=None, comment='最后修改管理员（用户名/ID）')
